"""Phase A — ingest.

    files ──▶ 1 ingest ──▶ 2 detect ──▶ 3 parse ──▶ 4 normalize ──▶ 5 redact ──▶ Entry[]

Everything after this phase — enrichment, correlation, the digest, root-cause
localization — consumes `Entry` and never touches raw text again. That is the
whole point of the boundary: a new log format is a new parser, and no later
stage learns about it.

Deterministic and side-effect free. Same bytes in, same entries out, no network
and no model.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from ingest import normalize as _normalize
from ingest.detect import FORMATS, Detection, detect
from ingest.entry import LEVELS, ROLE_DEPTH, ROLES, Entry, SourceFile
from ingest.limits import (
    MAX_FILES,
    MAX_PASTE_BYTES,
    MAX_PASTE_LINES,
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_LINES,
)
from ingest.parsers import ParseContext, parse
from ingest.prompt import to_prompt
from ingest.redact import RedactionPolicy, RedactionReport, redact_entries


@dataclass(slots=True)
class FileReport:
    """What ingest made of one file. Rendered as the per-file row in the UI."""

    file_id: str
    name: str
    service: str
    role: str
    format: str
    format_confidence: float
    flavor: str | None
    #: True when the user chose the format rather than detection guessing it.
    format_overridden: bool
    entry_count: int
    line_count: int
    bytes: int
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    #: Entries whose time was assumed or carried rather than read off the line.
    unresolved_timestamps: int = 0
    applied_offset_seconds: int = 0
    skew_warning: str | None = None
    #: Lines dropped because the batch hit its line cap.
    dropped_lines: int = 0
    #: Entries carrying real trace context — the difference between a
    #: reconstruction and a rendering, so it is surfaced per file.
    with_trace_id: int = 0
    #: Entries carrying at least one explicit join key.
    with_correlation_key: int = 0


@dataclass(slots=True)
class IngestResult:
    entries: list[Entry] = field(default_factory=list)
    files: list[FileReport] = field(default_factory=list)
    redaction: RedactionReport = field(default_factory=RedactionReport)
    skew_suspected: bool = False
    dropped_lines: int = 0

    @property
    def entry_count(self) -> int:
        return len(self.entries)

    @property
    def trace_coverage(self) -> float:
        """Share of entries carrying a real trace ID, 0.0–1.0.

        The single most important number this phase produces. At 1.0 the next
        phase is a rendering job; at 0.0 every link between services has to be
        inferred, and the UI should set expectations accordingly.
        """
        if not self.entries:
            return 0.0
        return sum(1 for e in self.entries if e.trace_id) / len(self.entries)

    def time_range(self) -> tuple[datetime | None, datetime | None]:
        stamps = [e.timestamp for e in self.entries if e.timestamp is not None]
        return (min(stamps), max(stamps)) if stamps else (None, None)

    def to_dicts(self) -> list[dict]:
        return [e.to_dict() for e in self.entries]


class TooManyFiles(ValueError):
    def __init__(self, count: int) -> None:
        super().__init__(f"{count} files exceeds the {MAX_FILES}-file limit")


def _truncate(text: str, max_bytes: int) -> str:
    encoded = text.encode()
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode(errors="ignore")


def ingest(
    files: Sequence[SourceFile],
    *,
    policy: RedactionPolicy | None = None,
    max_lines: int = MAX_UPLOAD_LINES,
    max_bytes: int = MAX_UPLOAD_BYTES,
) -> IngestResult:
    """Run stages 1–5 over a set of files.

    The line budget is spent in file order and what overflows is reported per
    file, never dropped quietly: a diagnosis built on a silently truncated log
    is worse than no diagnosis, because it looks the same.
    """
    if len(files) > MAX_FILES:
        raise TooManyFiles(len(files))

    policy = policy or RedactionPolicy()
    result = IngestResult()

    # RFC 3164 omits the year, so a syslog file borrows one from whichever
    # file in the batch does record it.
    year = _normalize.reference_year([f.content for f in files])

    remaining = max_lines
    clocks: list[_normalize.FileClock] = []

    for index, source in enumerate(files):
        file_id = f"f{index}"
        text = _truncate(source.content, max_bytes)

        detection: Detection = detect(text, override=source.format)
        service = source.resolved_service()
        role = source.role if source.role in ROLES else "unknown"

        ctx = ParseContext(
            file_id=file_id,
            service=service,
            role=role,
            service_declared=bool(source.service),
            reference_year=year,
            flavor=detection.flavor,
        )

        entries: list[Entry] = []
        dropped = 0
        for entry in parse(text, detection.format, ctx):
            if remaining <= 0:
                dropped += 1
                continue
            entries.append(entry)
            remaining -= 1

        clock = _normalize.resolve_file(
            entries,
            file_id=file_id,
            tz_name=source.timezone,
            offset_seconds=source.offset_seconds,
        )
        clocks.append(clock)

        result.entries.extend(entries)
        result.dropped_lines += dropped
        result.files.append(
            FileReport(
                file_id=file_id,
                name=source.name,
                service=service,
                role=role,
                format=detection.format,
                format_confidence=round(detection.confidence, 3),
                flavor=detection.flavor,
                format_overridden=source.format is not None,
                entry_count=len(entries),
                line_count=text.count("\n") + 1 if text else 0,
                bytes=len(text.encode()),
                first_timestamp=clock.first,
                last_timestamp=clock.last,
                unresolved_timestamps=clock.unresolved,
                applied_offset_seconds=clock.applied_offset_seconds,
                dropped_lines=dropped,
                with_trace_id=sum(1 for e in entries if e.trace_id),
                with_correlation_key=sum(1 for e in entries if e.correlation_keys),
            )
        )

    result.skew_suspected = _normalize.detect_skew(clocks)
    for report, clock in zip(result.files, clocks):
        report.skew_warning = clock.skew_warning

    result.redaction = redact_entries(result.entries, policy)
    _normalize.sort_entries(result.entries)
    return result


def ingest_text(
    text: str,
    *,
    name: str = "pasted",
    policy: RedactionPolicy | None = None,
) -> IngestResult:
    """Ingest a single paste — one unlabelled file, at paste-sized limits.

    The service is left undeclared on purpose. A paste is usually several
    services' output interleaved, and the parsers read the service off each
    line (a bracketed logger, a JSON `service` field) when no file-level label
    overrides them.
    """
    return ingest(
        [SourceFile(name=name, content=text)],
        policy=policy,
        max_lines=MAX_PASTE_LINES,
        max_bytes=MAX_PASTE_BYTES,
    )


__all__ = [
    "FORMATS",
    "LEVELS",
    "MAX_FILES",
    "MAX_PASTE_BYTES",
    "MAX_PASTE_LINES",
    "MAX_UPLOAD_BYTES",
    "MAX_UPLOAD_LINES",
    "ROLES",
    "ROLE_DEPTH",
    "Detection",
    "Entry",
    "FileReport",
    "IngestResult",
    "ParseContext",
    "RedactionPolicy",
    "RedactionReport",
    "SourceFile",
    "TooManyFiles",
    "detect",
    "ingest",
    "ingest_text",
    "parse",
    "to_prompt",
]
