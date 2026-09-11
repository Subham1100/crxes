"""Ingest — run Phase A over a file set and report what it found.

Two ways in, because they have different constraints. The JSON route suits a
paste and small files the browser already holds in memory. The multipart route
suits real incident dumps, where base64-ing tens of megabytes through a JSON
body would double the payload for nothing.

Neither persists anything. Preview is a pure function of its input, so the UI
can call it on every change to the file list — retitle a service, correct a
timezone, flip a redaction rule — and see the effect immediately.
"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile

from api.deps import get_current_user
from db.models import User
from exceptions import FileTooLarge, NoLogLines, TooManyFilesUploaded, UnreadableUpload
from ingest import FORMATS, ROLES, TooManyFiles, ingest
from ingest.limits import MAX_FILES, MAX_UPLOAD_BYTES
from schemas.ingest import FileIn, IngestPreviewOut, IngestRequest, RedactionOptions

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


@router.get("/formats")
async def list_formats() -> dict[str, list[str]]:
    """What the upload UI populates its dropdowns from.

    Served rather than hardcoded in the client so a new parser shows up in the
    picker without a frontend release.
    """
    return {"formats": list(FORMATS), "roles": list(ROLES)}


@router.post("/preview", response_model=IngestPreviewOut)
async def preview(
    body: IngestRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> IngestPreviewOut:
    """Detect, parse, normalize and redact — without persisting anything."""
    try:
        result = ingest(
            [f.to_source() for f in body.files],
            policy=body.redaction.to_policy(),
        )
    except TooManyFiles as exc:
        raise TooManyFilesUploaded(len(body.files)) from exc

    if not result.entries:
        raise NoLogLines()
    return IngestPreviewOut.of(result, sample_limit=body.sample_limit)


def _parse_meta(raw: str | None) -> dict[str, dict]:
    """Read the per-file metadata sidecar, keyed by filename.

    Multipart has nowhere to hang structured metadata on a part, so the client
    sends one JSON array describing the files by name. A malformed sidecar is
    not fatal — the files still parse, they just parse without labels, which
    is strictly better than refusing the upload.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, list):
        return {}
    return {
        item["name"]: item
        for item in parsed
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }


@router.post("/upload", response_model=IngestPreviewOut)
async def upload(
    user: Annotated[User, Depends(get_current_user)],
    files: Annotated[list[UploadFile], File()],
    #: JSON array of `FileIn` fields minus `content`, matched to parts by name.
    meta: Annotated[str | None, Form()] = None,
    redaction: Annotated[str | None, Form()] = None,
) -> IngestPreviewOut:
    if len(files) > MAX_FILES:
        raise TooManyFilesUploaded(len(files))

    metadata = _parse_meta(meta)
    options = RedactionOptions()
    if redaction:
        try:
            options = RedactionOptions.model_validate_json(redaction)
        except ValueError:
            # Fall back to the safe default rather than the caller's intent:
            # an unparseable redaction policy must never mean "redact nothing".
            options = RedactionOptions()

    sources: list[FileIn] = []
    for upload_file in files:
        raw = await upload_file.read()
        if len(raw) > MAX_UPLOAD_BYTES:
            raise FileTooLarge(upload_file.filename or "file", len(raw))
        try:
            # Logs are not reliably UTF-8 — a stray byte from a binary payload
            # is common and is not a reason to reject the file.
            text = raw.decode("utf-8", errors="replace")
        except (UnicodeError, AttributeError) as exc:
            raise UnreadableUpload(upload_file.filename or "file") from exc

        name = upload_file.filename or "upload"
        fields = {k: v for k, v in metadata.get(name, {}).items() if k != "name"}
        sources.append(FileIn(name=name, content=text, **fields))

    try:
        result = ingest(
            [f.to_source() for f in sources], policy=options.to_policy()
        )
    except TooManyFiles as exc:
        raise TooManyFilesUploaded(len(sources)) from exc

    if not result.entries:
        raise NoLogLines()
    return IngestPreviewOut.of(result)
