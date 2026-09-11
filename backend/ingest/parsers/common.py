"""Helpers every parser shares: timestamps, levels, and entry folding.

The hot loop of the whole ingest phase runs through this module, so the
implementations lean on C-level calls — `datetime.fromisoformat`, compiled
`re`, `str.split` — rather than hand-rolled index arithmetic, which is slower
in CPython however tight it looks on the page.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator

from ingest.limits import MAX_CONTINUATION_LINES, MAX_LINE_CHARS

# --- Levels ------------------------------------------------------------------

#: Every spelling of a severity we have seen, mapped onto `entry.LEVELS`.
_LEVEL_ALIASES: dict[str, str] = {
    "TRACE": "trace",
    "FINEST": "trace",
    "VERBOSE": "trace",
    "DEBUG": "debug",
    "FINE": "debug",
    "DBG": "debug",
    "INFO": "info",
    "INFORMATION": "info",
    "INFORMATIONAL": "info",
    "NOTICE": "info",
    "LOG": "info",
    "STATEMENT": "info",
    "DETAIL": "info",
    "CONTEXT": "info",
    "HINT": "info",
    "WARN": "warn",
    "WARNING": "warn",
    "ERROR": "error",
    "ERR": "error",
    "SEVERE": "error",
    "EMERG": "fatal",
    "EMERGENCY": "fatal",
    "ALERT": "critical",
    "CRIT": "critical",
    "CRITICAL": "critical",
    "FATAL": "fatal",
    "PANIC": "fatal",
    "DEFAULT": "info",
}

_LEVEL_TOKEN_RE = re.compile(
    rf"\b({'|'.join(sorted(_LEVEL_ALIASES, key=len, reverse=True))})\b", re.IGNORECASE
)

#: Syslog severity (the low 3 bits of PRI) → canonical level.
_SYSLOG_SEVERITY: tuple[str, ...] = (
    "fatal",  # 0 emerg
    "critical",  # 1 alert
    "critical",  # 2 crit
    "error",  # 3 err
    "warn",  # 4 warning
    "info",  # 5 notice
    "info",  # 6 info
    "debug",  # 7 debug
)


def level_from_text(text: str, default: str = "info") -> tuple[str, int, int]:
    """Find a severity word in `text`.

    Returns the level plus the match span, so callers can strip the token out
    of the message without searching twice. The span is `(-1, -1)` when nothing
    matched.
    """
    match = _LEVEL_TOKEN_RE.search(text)
    if match is None:
        return default, -1, -1
    return _LEVEL_ALIASES[match.group(1).upper()], match.start(), match.end()


def level_from_name(name: str | None, default: str = "info") -> str:
    """Map an explicit severity field (`"WARNING"`, `"err"`) onto a level."""
    if not name:
        return default
    return _LEVEL_ALIASES.get(str(name).strip().upper(), default)


def level_from_syslog_priority(pri: int) -> str:
    return _SYSLOG_SEVERITY[pri & 0x07]


#: OTel SeverityNumber bands, 1-indexed: 1–4 TRACE, 5–8 DEBUG, 9–12 INFO,
#: 13–16 WARN, 17–20 ERROR, 21–24 FATAL.
_OTEL_SEVERITY: tuple[str, ...] = ("trace", "debug", "info", "warn", "error", "fatal")


def level_from_number(value: Any, scale: str = "auto", default: str = "info") -> str:
    """Map a numeric severity onto a level.

    Three scales collide in the 10–24 band and no heuristic separates them:
    OTel's SeverityNumber puts 10 in DEBUG's range while Python's `logging`
    makes 10 exactly DEBUG and 20 INFO — but OTel calls 20 ERROR. So the
    caller passes `scale`, which it knows from the JSON flavor it detected.
    `"auto"` reads ≤24 as OTel and anything above as the decade scale that
    bunyan, pino and `logging` share.
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if n <= 0:
        return default

    if scale == "otel" or (scale == "auto" and n <= 24):
        return _OTEL_SEVERITY[min(n - 1, 23) // 4]

    # Decade scale: bunyan/pino 10–60, Python logging 10–50.
    if n >= 60:
        return "fatal"
    if n >= 50:
        return "critical"
    if n >= 40:
        return "error"
    if n >= 30:
        return "warn"
    if n >= 20:
        return "info"
    return "debug"


# --- Timestamps --------------------------------------------------------------

#: ISO-8601, with `T` or a space, optional fraction, optional offset. Also the
#: shape Postgres and most structured loggers emit.
_ISO_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?(?:Z|[+-]\d{2}:?\d{2})?)"
)
#: CLF: `04/Aug/2026:14:58:11 +0000`
_CLF_TS_RE = re.compile(r"(\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}\s*[+-]\d{4})")
#: RFC 3164: `Aug  4 14:58:11` — no year, no zone.
_SYSLOG_TS_RE = re.compile(r"([A-Z][a-z]{2}\s+\d{1,2} \d{2}:\d{2}:\d{2})")

_MONTHS: dict[str, int] = {
    m: i
    for i, m in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1
    )
}


def parse_iso(raw: str) -> datetime | None:
    """Parse an ISO-8601 timestamp. Returns naive or aware, as written."""
    # `fromisoformat` in 3.11+ takes `Z` and unpadded offsets; the comma
    # decimal separator (ISO 8601 permits it, Java loggers emit it) it does
    # not. Nanosecond precision has to be clipped to microseconds.
    text = raw.replace(",", ".")
    if len(text) > 26 and "." in text:
        head, _, tail = text.partition(".")
        digits = tail[:6]
        rest = tail[len(digits) :].lstrip("0123456789")
        text = f"{head}.{digits}{rest}"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_clf(raw: str) -> datetime | None:
    """Parse `04/Aug/2026:14:58:11 +0000`."""
    try:
        day, mon, rest = raw.split("/", 2)
        year, hh, mm, ss_off = rest.split(":", 3)
        ss, _, offset = ss_off.partition(" ")
        sign = 1 if offset.strip().startswith("+") else -1
        off = offset.strip()[1:]
        delta = timedelta(hours=int(off[:2]), minutes=int(off[2:4])) * sign
        return datetime(
            int(year),
            _MONTHS[mon.title()],
            int(day),
            int(hh),
            int(mm),
            int(ss),
            tzinfo=timezone(delta),
        )
    except (ValueError, KeyError, IndexError):
        return None


def parse_syslog_ts(raw: str, reference_year: int) -> datetime | None:
    """Parse `Aug  4 14:58:11`, which carries no year and no zone.

    RFC 3164 omits the year, so one has to be supplied. `reference_year` comes
    from the rest of the batch where possible; see `normalize.reference_year`.
    Returns a naive datetime — the file's declared timezone resolves it later.
    """
    try:
        mon, day, clock = raw.split(None, 2)
        hh, mm, ss = clock.split(":")
        return datetime(reference_year, _MONTHS[mon.title()], int(day), int(hh), int(mm), int(ss))
    except (ValueError, KeyError):
        return None


def leading_timestamp(text: str, reference_year: int = 0) -> tuple[datetime | None, str]:
    """Pull a timestamp off the front of `text`; return it and the remainder.

    Only the first 64 characters are searched. A timestamp further in than that
    belongs to the message — an exception that quotes a deadline, say — and
    treating it as the entry's own time would misorder the timeline.
    """
    head = text[:64]

    match = _ISO_RE.search(head)
    if match and match.start() <= 1:
        parsed = parse_iso(match.group(1))
        if parsed is not None:
            return parsed, text[match.end() :].lstrip(" \t:|-")

    match = _CLF_TS_RE.search(head)
    if match:
        parsed = parse_clf(match.group(1))
        if parsed is not None:
            return parsed, text[match.end() :].lstrip(" \t:|-")

    if reference_year:
        match = _SYSLOG_TS_RE.search(head)
        if match and match.start() <= 1:
            parsed = parse_syslog_ts(match.group(1), reference_year)
            if parsed is not None:
                return parsed, text[match.end() :].lstrip(" \t:|-")

    return None, text


def coerce_scalar(raw: str) -> Any:
    """Type a bare token from a structured format.

    Only unambiguous cases: integers, floats, booleans, null. Anything with a
    unit attached (`5012ms`, `1.71GB`) stays a string — pulling numbers out of
    units is the enrich phase's job, and it needs the unit to do it.
    """
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("null", "nil", "none"):
        return None
    first = raw[0]
    if first.isdigit() or (first in "+-" and len(raw) > 1):
        try:
            return int(raw)
        except ValueError:
            try:
                return float(raw)
            except ValueError:
                return raw
    return raw


# --- Folding -----------------------------------------------------------------

#: A line that is plainly a continuation regardless of format: an indented
#: stack frame, a JVM `Caused by:`, a Python traceback body, an elided frame
#: count.
_CONTINUATION_RE = re.compile(
    r"^(?:\s+"
    r"|Caused by:"
    r"|Traceback \(most recent call last\)"
    r"|\.{3}\s*\d*\s*more"
    r"|at\s+[\w$.<>]+\("
    r"|File \""
    r")"
)


def is_continuation(line: str) -> bool:
    return bool(_CONTINUATION_RE.match(line))


def fold(
    text: str,
    starts_entry: Callable[[str], bool],
) -> Iterator[tuple[int, str, list[str]]]:
    """Group raw lines into entry blocks.

    Yields `(line_no, first_line, continuation_lines)`. A line is a
    continuation when the format's own prefix does not match it — which is a
    far more reliable test than guessing at indentation, because every
    line-oriented format here has an unmistakable prefix. `starts_entry` falls
    back to `is_continuation` for plain text, where there is no prefix to test.

    Leading lines that arrive before any entry has started (a banner, a
    truncated first record) are emitted as their own entry rather than
    discarded — a dropped line during an incident is worse than an ugly one.
    """
    line_no = 0
    pending_no = 0
    pending: str | None = None
    extra: list[str] = []
    overflow = 0

    for line in text.splitlines():
        line_no += 1
        if len(line) > MAX_LINE_CHARS:
            line = line[:MAX_LINE_CHARS] + " …[line truncated]"
        if not line.strip():
            continue

        if pending is None:
            pending, pending_no = line, line_no
            continue

        if starts_entry(line):
            yield pending_no, pending, extra
            pending, pending_no, extra, overflow = line, line_no, [], 0
        elif len(extra) < MAX_CONTINUATION_LINES:
            extra.append(line)
        else:
            overflow += 1
            if overflow == 1:
                extra.append(f"…[{MAX_CONTINUATION_LINES}-line continuation cap reached]")

    if pending is not None:
        yield pending_no, pending, extra
