"""CRI — the on-disk format behind `kubectl logs`.

    2026-08-04T14:58:11.004Z stdout F the line the container actually wrote

An envelope, not a log format. The kubelet stamps its own receive time, the
stream, and a partial/full flag onto whatever the container emitted, so the
real work is unwrapping it and parsing the payload — which is usually JSON or
plain text, and is dispatched accordingly.

The `P` flag marks a line the runtime split at its 16KB read boundary. Those
fragments are rejoined before parsing; a stack trace chopped mid-frame parses
as garbage otherwise.
"""

import json
import re
from typing import TYPE_CHECKING, Iterator

from ingest.entry import Entry
from ingest.parsers.common import parse_iso
from ingest.parsers.plain import build_entry

if TYPE_CHECKING:
    from ingest.parsers import ParseContext

_CRI_RE = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:\d{2}))\s+"
    r"(?P<stream>stdout|stderr)\s+"
    r"(?P<flag>[FP])\s"
    r"(?P<message>.*)$",
    re.DOTALL,
)


def parse(text: str, ctx: "ParseContext") -> Iterator[Entry]:
    # Imported here rather than at module scope: `jsonl` imports `plain`, which
    # this module also imports, and routing the payload back through `jsonl`
    # at import time would close the cycle.
    from ingest.parsers import jsonl

    def build(
        parts: list[str], raw_lines: list[str], line_no: int, raw_time: str, stream: str
    ) -> Entry | None:
        """Turn accumulated fragments into one entry.

        Takes its state as arguments rather than closing over the loop's
        variables: a generator that reads enclosing locals runs its body
        lazily, after those locals have already been reset for the next entry.
        """
        if not parts:
            return None
        payload = "".join(parts)
        timestamp = parse_iso(raw_time) if raw_time else None
        stripped = payload.strip()
        # `raw` is what the file actually held, envelope included — the UI
        # drills back to the original bytes, not to the unwrapped payload.
        raw = "\n".join(raw_lines)

        if stripped.startswith("{"):
            try:
                doc = json.loads(stripped)
            except ValueError:
                doc = None
            if isinstance(doc, dict):
                # The container logs JSON. Parse the payload on its own terms,
                # keeping the kubelet timestamp only as a fallback.
                inner = next(jsonl.parse(stripped, ctx), None)
                if inner is not None:
                    inner.line_no = line_no
                    if inner.timestamp is None:
                        inner.timestamp = timestamp
                        inner.time_source = "offset" if timestamp else "none"
                    inner.attributes.setdefault("stream", stream)
                    inner.raw = raw
                    return inner

        entry = build_entry(payload.rstrip("\n"), [], line_no, ctx, fallback_ts=timestamp)
        entry.raw = raw
        entry.attributes.setdefault("stream", stream)
        # stderr is a failure signal in its own right for containers that log
        # unlevelled text, but never a reason to downgrade a parsed severity.
        if stream == "stderr" and entry.level == "info":
            entry.level = "warn"
        return entry

    line_no = 0
    parts: list[str] = []
    raw_lines: list[str] = []
    pending_no = 0
    pending_time = ""
    pending_stream = ""

    for line in text.splitlines():
        line_no += 1
        if not line.strip():
            continue

        match = _CRI_RE.match(line)
        if match is None:
            flushed = build(parts, raw_lines, pending_no, pending_time, pending_stream)
            if flushed is not None:
                yield flushed
            parts, raw_lines, pending_time, pending_stream = [], [], "", ""
            yield build_entry(line, [], line_no, ctx)
            continue

        fields = match.groupdict()
        # A fragment from the other stream interleaved into ours ends the
        # partial: stdout and stderr are separate byte streams and never
        # continue one another.
        if parts and fields["stream"] != pending_stream:
            flushed = build(parts, raw_lines, pending_no, pending_time, pending_stream)
            if flushed is not None:
                yield flushed
            parts, raw_lines = [], []

        if not parts:
            pending_no = line_no
            pending_time = fields["time"]
            pending_stream = fields["stream"]

        # Partials are joined without a newline — the runtime split the line
        # at its read boundary, not at a line break.
        parts.append(fields["message"])
        raw_lines.append(line)

        if fields["flag"] == "F":
            flushed = build(parts, raw_lines, pending_no, pending_time, pending_stream)
            if flushed is not None:
                yield flushed
            parts, raw_lines, pending_time, pending_stream = [], [], "", ""

    flushed = build(parts, raw_lines, pending_no, pending_time, pending_stream)
    if flushed is not None:
        yield flushed


__all__ = ["parse"]
