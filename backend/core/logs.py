"""Turn a pasted blob of log text into NormalizedLogEntry dicts.

The provider integrations in Phase 4 will emit the same shape from their own
APIs, so everything downstream — agents, storage, the UI — only ever sees
normalized entries. This module is the `provider="manual"` implementation.
"""

import re
from datetime import datetime, timezone
from typing import Any

#: Guards against a paste large enough to blow out the request or the context
#: window. Anything past this is dropped and reported back as a line count.
MAX_LINES = 5_000
MAX_BYTES = 2_000_000

_LEVELS = {
    "FATAL": "fatal",
    "CRIT": "critical",
    "CRITICAL": "critical",
    "ERROR": "error",
    "ERR": "error",
    "WARN": "warn",
    "WARNING": "warn",
    "INFO": "info",
    "NOTICE": "info",
    "DEBUG": "debug",
    "TRACE": "trace",
}

_LEVEL_RE = re.compile(rf"\b({'|'.join(_LEVELS)})\b", re.IGNORECASE)

#: Leading timestamp, in the formats that actually show up in pasted logs:
#: ISO-8601 (with T or space), bracketed ISO, and syslog's "Aug  4 15:04:05".
_TIMESTAMP_RES = (
    re.compile(r"^\[?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\]?"),
    re.compile(r"^\[?(\d{2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2}\s*[+-]\d{4})\]?"),
    re.compile(r"^(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})"),
)

#: A bracketed or parenthesised token that isn't a timestamp or a level —
#: conventionally the logger or service name.
_SOURCE_RE = re.compile(r"[\[(]([\w.\-/:]{2,64})[\])]")

#: A stack-trace or continuation line belongs to the entry above it.
_CONTINUATION_RE = re.compile(r"^(\s+|Caused by:|\.\.\.\s|at\s+[\w$.]+\()")


def _parse_timestamp(line: str) -> tuple[datetime | None, str]:
    """Pull a leading timestamp off `line`; return it plus the remainder."""
    for pattern in _TIMESTAMP_RES:
        match = pattern.match(line)
        if not match:
            continue
        raw = match.group(1)
        rest = line[match.end() :].lstrip(" \t-|:")
        try:
            parsed = datetime.fromisoformat(raw.replace(",", ".").replace("Z", "+00:00"))
        except ValueError:
            # Syslog and Apache formats aren't ISO — keep the entry, drop the
            # timestamp rather than discarding a perfectly good log line.
            return None, rest
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed, rest
    return None, line


def _parse_level(text: str) -> tuple[str, str]:
    """Return the normalized level and the text with the level token removed."""
    match = _LEVEL_RE.search(text[:80])
    if not match:
        return "info", text
    level = _LEVELS[match.group(1).upper()]
    # Leave brackets alone — _parse_source still needs them to find the logger.
    stripped = (text[: match.start()] + text[match.end() :]).strip(" \t-|:")
    return level, stripped or text


def _parse_source(text: str) -> tuple[str, str]:
    """Return the logger/service name and the text with that token removed."""
    match = _SOURCE_RE.search(text[:120])
    if not match:
        return "manual", text
    candidate = match.group(1)
    # A bare number or a level word in brackets isn't a logger name.
    if candidate.isdigit() or candidate.upper() in _LEVELS:
        return "manual", text
    stripped = (text[: match.start()] + text[match.end() :]).strip(" \t-|:")
    return candidate, stripped or text


def normalize(text: str) -> tuple[list[dict[str, Any]], int]:
    """Parse pasted log text.

    Returns the normalized entries and the number of raw lines that were
    dropped because the paste exceeded `MAX_LINES` / `MAX_BYTES`.
    """
    if len(text.encode()) > MAX_BYTES:
        text = text.encode()[:MAX_BYTES].decode(errors="ignore")

    lines = [line.rstrip() for line in text.splitlines()]
    lines = [line for line in lines if line.strip()]

    dropped = max(0, len(lines) - MAX_LINES)
    lines = lines[:MAX_LINES]

    entries: list[dict[str, Any]] = []
    for line in lines:
        # Stack frames and wrapped messages fold into the entry they belong to
        # so the agents see one event per entry, not one line per entry.
        if entries and _CONTINUATION_RE.match(line):
            entries[-1]["message"] += "\n" + line.strip()
            entries[-1]["raw"] += "\n" + line
            continue

        timestamp, rest = _parse_timestamp(line)
        level, rest = _parse_level(rest)
        source, message = _parse_source(rest)
        entries.append(
            {
                "timestamp": timestamp.isoformat() if timestamp else "",
                "level": level,
                "source": source,
                "message": message.strip(),
                "metadata": {},
                "raw": line,
            }
        )

    return entries, dropped


def to_prompt(entries: list[dict[str, Any]], max_chars: int = 60_000) -> str:
    """Render entries for an agent prompt, trimming the middle if oversized.

    The head holds the onset of an incident and the tail holds the failure, so
    when something has to go it is the repetitive middle.
    """
    rendered = [
        f"{e['timestamp'] or '-'} {e['level'].upper():<8} {e['source']}: {e['message']}"
        for e in entries
    ]
    body = "\n".join(rendered)
    if len(body) <= max_chars:
        return body

    half = max_chars // 2
    head, tail = body[:half], body[-half:]
    omitted = len(body) - max_chars
    return f"{head}\n\n… [{omitted:,} characters of the middle omitted] …\n\n{tail}"
