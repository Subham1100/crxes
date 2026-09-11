"""Freeform text — the format everything else falls back to.

There is no spec to follow here, so this parser is a pile of conventions that
hold across most human-readable loggers: a timestamp at the front, a severity
word near it, and the emitting component in brackets. Each is optional and
each failure is survivable; the one thing this parser will not do is drop a
line.
"""

import re
from datetime import datetime
from typing import TYPE_CHECKING, Iterator

from ingest.entry import Entry
from ingest.parsers.common import (
    fold,
    is_continuation,
    leading_timestamp,
    level_from_text,
    parse_iso,
)

if TYPE_CHECKING:
    from ingest.parsers import ParseContext

#: A bracketed or parenthesised token that is conventionally the logger or
#: component name: `[api.gateway]`, `(checkout.retry)`.
_LOGGER_RE = re.compile(r"[\[(]([\w.\-/:]{2,64})[\])]")

#: Levels, so a `[ERROR]` is not mistaken for a logger name.
_NOT_LOGGER = frozenset(
    {
        "TRACE",
        "DEBUG",
        "INFO",
        "WARN",
        "WARNING",
        "ERROR",
        "ERR",
        "CRIT",
        "CRITICAL",
        "FATAL",
        "PANIC",
        "NOTICE",
        "LOG",
    }
)


def extract_logger(text: str) -> tuple[str | None, str]:
    """Pull a bracketed component name off `text`; return it and the remainder."""
    match = _LOGGER_RE.search(text[:120])
    if match is None:
        return None, text
    candidate = match.group(1)
    if candidate.isdigit() or candidate.upper() in _NOT_LOGGER:
        return None, text
    stripped = (text[: match.start()] + text[match.end() :]).strip(" \t-|:")
    return candidate, stripped or text


def build_entry(
    line: str,
    extra: list[str],
    line_no: int,
    ctx: "ParseContext",
    fallback_ts: datetime | None = None,
) -> Entry:
    """Parse one freeform line (plus its continuations) into an `Entry`.

    Shared with the container parsers, which unwrap an envelope and hand the
    payload here along with the envelope's timestamp as `fallback_ts`.
    """
    timestamp, rest = leading_timestamp(line, ctx.reference_year)
    if timestamp is None and fallback_ts is not None:
        timestamp = fallback_ts

    level, start, end = level_from_text(rest[:80])
    if start >= 0:
        rest = (rest[:start] + rest[end:]).strip(" \t-|:")

    logger, message = extract_logger(rest)

    attributes: dict[str, object] = {}
    service = ctx.service
    if logger:
        # A file the user labelled keeps its label; an unlabelled one (a paste
        # of mixed output) takes the logger as its service, which is the only
        # per-line service signal freeform text offers.
        if ctx.service_declared:
            attributes["logger"] = logger
        else:
            service = logger

    raw = "\n".join((line, *extra)) if extra else line
    if extra:
        message = message.strip() + "\n" + "\n".join(part.strip() for part in extra)

    return Entry(
        file_id=ctx.file_id,
        line_no=line_no,
        service=service,
        role=ctx.role,
        timestamp=timestamp,
        time_source="offset" if timestamp and timestamp.tzinfo else ("none" if not timestamp else "naive"),
        level=level,
        message=message.strip(),
        attributes=attributes,
        raw=raw,
    )


def parse(text: str, ctx: "ParseContext") -> Iterator[Entry]:
    for line_no, line, extra in fold(text, lambda candidate: not is_continuation(candidate)):
        yield build_entry(line, extra, line_no, ctx)


__all__ = ["build_entry", "extract_logger", "parse", "parse_iso"]
