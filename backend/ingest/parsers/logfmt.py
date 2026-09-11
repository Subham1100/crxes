"""logfmt — `ts=… level=info msg="…" key=value`.

Heroku, the Go ecosystem and the Grafana stack all emit this. It is structured
enough to read fields out of directly, so unlike plain text there is no
guessing here: the keys say what they are.

A line that also carries free text outside its pairs (a prefix, a trailing
message) keeps that text as the message when no `msg` key is present.
"""

import re
from typing import TYPE_CHECKING, Iterator

from ingest.entry import Entry
from ingest.parsers.common import (
    coerce_scalar,
    fold,
    is_continuation,
    leading_timestamp,
    level_from_name,
    parse_iso,
)

if TYPE_CHECKING:
    from ingest.parsers import ParseContext

_PAIR_RE = re.compile(r'([A-Za-z_][\w.\-]*)=("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|[^\s]*)')

_TIME_KEYS = ("ts", "time", "timestamp", "t", "@timestamp")
_LEVEL_KEYS = ("level", "lvl", "severity", "sev")
_MESSAGE_KEYS = ("msg", "message", "event", "text")
_SERVICE_KEYS = ("service", "svc", "component", "logger", "caller", "app", "name")
_TRACE_KEYS = ("trace_id", "traceid", "trace", "dd.trace_id")
_SPAN_KEYS = ("span_id", "spanid", "span", "dd.span_id")
_CORRELATION_KEYS = (
    "request_id",
    "requestid",
    "req_id",
    "correlation_id",
    "session_id",
    "user_id",
    "pid",
    "org_id",
    "tenant",
)


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        return inner.replace('\\"', '"').replace("\\'", "'").replace("\\n", "\n").replace("\\\\", "\\")
    return value


def _pop(pairs: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if key in pairs:
            return pairs.pop(key)
    return None


def _starts_entry(line: str) -> bool:
    """A new logfmt record has pairs of its own and is not indented."""
    if line[:1].isspace() or is_continuation(line):
        return False
    return bool(_PAIR_RE.search(line))


def parse(text: str, ctx: "ParseContext") -> Iterator[Entry]:
    for line_no, line, extra in fold(text, _starts_entry):
        pairs: dict[str, str] = {}
        spans: list[tuple[int, int]] = []
        for match in _PAIR_RE.finditer(line):
            pairs[match.group(1).lower()] = _unquote(match.group(2))
            spans.append(match.span())

        # Text outside the pairs — a prefix some loggers prepend, or a bare
        # message tail. Reassembled in order so it reads as it was written.
        leftover_parts: list[str] = []
        cursor = 0
        for start, end in spans:
            if start > cursor:
                leftover_parts.append(line[cursor:start])
            cursor = end
        leftover_parts.append(line[cursor:])
        leftover = " ".join(part.strip() for part in leftover_parts if part.strip())

        raw_ts = _pop(pairs, _TIME_KEYS)
        timestamp = parse_iso(raw_ts) if raw_ts else None
        if timestamp is None and raw_ts and raw_ts.isdigit():
            # Some emitters write epoch seconds here.
            from ingest.parsers.jsonl import _epoch_to_datetime

            timestamp = _epoch_to_datetime(float(raw_ts))
        if timestamp is None:
            timestamp, leftover = leading_timestamp(leftover, ctx.reference_year)

        message = _pop(pairs, _MESSAGE_KEYS) or leftover
        level = level_from_name(_pop(pairs, _LEVEL_KEYS))

        service = ctx.service
        service_value = _pop(pairs, _SERVICE_KEYS)
        if service_value and not ctx.service_declared:
            service = service_value

        # Popped before `attributes` is built, or they would appear both as
        # first-class fields and as leftover attributes.
        trace_id = _pop(pairs, _TRACE_KEYS)
        span_id = _pop(pairs, _SPAN_KEYS)

        correlation = {key: pairs[key] for key in _CORRELATION_KEYS if key in pairs}
        attributes = {key: coerce_scalar(value) for key, value in pairs.items()}
        if service_value and ctx.service_declared:
            attributes.setdefault("logger", service_value)

        raw = "\n".join((line, *extra)) if extra else line
        if extra:
            message = f"{message}\n" + "\n".join(part.strip() for part in extra)

        yield Entry(
            file_id=ctx.file_id,
            line_no=line_no,
            service=service,
            role=ctx.role,
            timestamp=timestamp,
            time_source="offset" if timestamp and timestamp.tzinfo else ("none" if not timestamp else "naive"),
            level=level,
            message=message.strip(),
            trace_id=trace_id or None,
            span_id=span_id or None,
            attributes=attributes,
            correlation_keys=correlation,
            raw=raw,
        )


__all__ = ["parse"]
