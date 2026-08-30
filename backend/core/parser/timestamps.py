"""Timestamp scanning, written for throughput.

The first version of this file sliced fixed-width fields out by index and did
integer arithmetic, on the theory that it would beat `datetime.fromisoformat`.
It lost to it by a factor of ten. The measurement is in `bench.py micro`, and
the lesson generalises to everything else in this package:

    timestamps.scan (hand-rolled)   1667 ns
    datetime.fromisoformat           154 ns

Every `line[i].isdigit()` step and every separator skip is a full interpreter
loop iteration at roughly 50 ns, so a scanner that walks characters in Python
cannot win no matter how tight the arithmetic is. What wins is doing the walk
*once*, inside C: a single compiled regex consumes the whole timestamp — plus
its trailing punctuation, so `match.end()` already points at the body — and
Python only converts the captured groups.

Two caches then remove most of what is left. The date and the time-of-day are
each memoized on their own substring: a file covers a handful of days and at
most 86,400 distinct seconds, so at a million records both are hits nearly
every time, and a dict lookup is cheaper than three `int()` calls.

`fromisoformat` is still not used directly, for a reason unrelated to speed:
getting nanoseconds back out of a `datetime` means `dt.timestamp() * 1e9`,
which routes an integer through a float and silently loses precision below the
microsecond.

Every scanner returns `(unix_nanoseconds, end_index)`, with `(0, 0)` meaning
"no timestamp here". Returning the end index rather than the remaining text
keeps the caller from allocating a slice on a miss.
"""

from __future__ import annotations

import re
from datetime import date

NS_PER_SEC = 1_000_000_000

_EPOCH = date(1970, 1, 1)
_POW10 = (1, 10, 100, 1_000, 10_000, 100_000, 1_000_000, 10_000_000, 100_000_000)

_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

#: "2026-08-30" -> unix seconds at midnight UTC. Bounded by the distinct days
#: in the input, which is a handful.
#:
#: Public, along with `TOD_CACHE`, because `ingest`'s hot loop reads them
#: directly. A `day_seconds()` call is ~80 ns of interpreter overhead on top of
#: a dict lookup that nearly always hits, and at a million records that is a
#: tenth of a second spent on the function call alone.
DAY_CACHE: dict[str, int] = {}
_EPOCH_DAY = DAY_CACHE

#: "10:00:01" -> seconds since midnight. Bounded at 86,400 entries.
TOD_CACHE: dict[str, int] = {}
_TIME_OF_DAY = TOD_CACHE

#: "+05:30" -> seconds to subtract to reach UTC. Bounded at ~40 entries.
_ZONE: dict[str, int] = {}

#: Punctuation between a timestamp and whatever follows it. Consumed by the
#: patterns themselves so `match.end()` lands on the body with no Python loop.
TRAILING = r"[ \t\-|:>\]]*"

#: The ISO-8601 timestamp, exported so `ingest` can embed this exact
#: sub-pattern in its combined fast-path regex rather than restating it.
#: Groups, in order: date, time-of-day, fractional seconds, zone.
ISO_PATTERN = (
    r"\[?(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})"
    r"(?:[.,](\d{1,9}))?([Zz]|[+-]\d{2}:?\d{2})?\]?"
)

_ISO_RE = re.compile(ISO_PATTERN + TRAILING)

#: 04/Aug/2026:15:04:05 +0000 — Apache / nginx common log format.
_CLF_RE = re.compile(
    r"\[?(\d{2})/([A-Z][a-z]{2})/(\d{4}):(\d{2}:\d{2}:\d{2})"
    r"(?:\s*([+-]\d{2}:?\d{2}))?\]?" + TRAILING
)

#: Aug  4 15:04:05 — syslog RFC3164, which has no year in the format at all.
_SYSLOG_RE = re.compile(
    r"([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{2}:\d{2}:\d{2})(?:[.,](\d{1,9}))?" + TRAILING
)


def day_seconds(datestr: str) -> int:
    """Unix seconds at midnight UTC for `YYYY-MM-DD`. Raises on a bad date."""
    got = _EPOCH_DAY.get(datestr)
    if got is None:
        d = date(int(datestr[0:4]), int(datestr[5:7]), int(datestr[8:10]))
        got = (d - _EPOCH).days * 86_400
        _EPOCH_DAY[datestr] = got
    return got


def ymd_seconds(year: int, month: int, day: int) -> int:
    key = "%04d-%02d-%02d" % (year, month, day)
    got = _EPOCH_DAY.get(key)
    if got is None:
        got = (date(year, month, day) - _EPOCH).days * 86_400
        _EPOCH_DAY[key] = got
    return got


def time_of_day(tod: str) -> int:
    """Seconds since midnight for `HH:MM:SS`."""
    got = _TIME_OF_DAY.get(tod)
    if got is None:
        got = int(tod[0:2]) * 3600 + int(tod[3:5]) * 60 + int(tod[6:8])
        _TIME_OF_DAY[tod] = got
    return got


def zone_seconds(zone: str) -> int:
    """Offset for `+05:30` / `-0400`, as seconds to subtract to reach UTC."""
    got = _ZONE.get(zone)
    if got is None:
        body = zone[1:].replace(":", "")
        got = (int(body[0:2]) * 3600 + int(body[2:4] or 0) * 60) * (1 if zone[0] == "+" else -1)
        _ZONE[zone] = got
    return got


def nanos_from_parts(
    day: int, tod: str, fraction: str | None, zone: str | None
) -> int:
    """Assemble unix nanoseconds from a matched timestamp's pieces.

    `day` is already in seconds (from `day_seconds`/`ymd_seconds`). An absent
    zone is treated as UTC, which is how these logs are almost always written
    and how `core.logs` already behaved.
    """
    secs = day + time_of_day(tod)
    if zone is not None and zone != "Z" and zone != "z":
        secs -= zone_seconds(zone)
    nanos = secs * NS_PER_SEC
    if fraction:
        nanos += int(fraction) * _POW10[9 - len(fraction)]
    return nanos


_FALLBACK_YEAR = 0


def _fallback_year() -> int:
    """Syslog omits the year, so one has to be assumed. Cached — `date.today()`
    hits the clock, and this would otherwise run once per syslog line."""
    global _FALLBACK_YEAR
    if not _FALLBACK_YEAR:
        _FALLBACK_YEAR = date.today().year
    return _FALLBACK_YEAR


def scan(line: str, default_year: int = 0) -> tuple[int, int]:
    """Find a leading timestamp. Returns (unix nanoseconds, index after it).

    ISO-8601 is tried first: it is by far the most common, and a non-matching
    line is rejected inside the regex engine rather than by Python code.
    """
    match = _ISO_RE.match(line)
    if match is not None:
        try:
            day = day_seconds(match.group(1))
        except ValueError:
            return 0, 0
        return nanos_from_parts(day, match.group(2), match.group(3), match.group(4)), match.end()

    match = _CLF_RE.match(line)
    if match is not None:
        month = _MONTHS.get(match.group(2))
        if month is not None:
            try:
                day = ymd_seconds(int(match.group(3)), month, int(match.group(1)))
            except ValueError:
                return 0, 0
            return nanos_from_parts(day, match.group(4), None, match.group(5)), match.end()

    match = _SYSLOG_RE.match(line)
    if match is not None:
        month = _MONTHS.get(match.group(1))
        if month is not None:
            try:
                day = ymd_seconds(default_year or _fallback_year(), month, int(match.group(2)))
            except ValueError:
                return 0, 0
            return nanos_from_parts(day, match.group(3), match.group(4), None), match.end()

    return 0, 0


def coerce(value: object, default_year: int = 0) -> int:
    """Turn a JSON timestamp field into unix nanoseconds.

    Structured sources disagree about units — OTLP writes nanoseconds as a
    string, Elastic writes ISO-8601, most application loggers write epoch
    seconds or milliseconds as a number — so the unit is inferred from
    magnitude. The thresholds sit far from any plausible real timestamp: epoch
    *seconds* will not reach 1e11 until the year 5138.
    """
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, int):
        n = value
    elif isinstance(value, float):
        n = int(value * NS_PER_SEC)
        return n if n > 0 else 0
    elif isinstance(value, str):
        if value.isdigit():
            n = int(value)
        else:
            nanos, _ = scan(value, default_year)
            return nanos
    else:
        return 0

    if n <= 0:
        return 0
    if n >= 100_000_000_000_000_000:  # 1e17 — nanoseconds
        return n
    if n >= 100_000_000_000_000:  # 1e14 — microseconds
        return n * 1_000
    if n >= 100_000_000_000:  # 1e11 — milliseconds
        return n * 1_000_000
    return n * NS_PER_SEC
