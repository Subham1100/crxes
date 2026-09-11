"""Stage 4 — make timestamps comparable across files.

Nothing downstream works until this stage does. Correlation orders events
causally, the digest buckets them, and root-cause localization asks which
service moved first — all three are asking the same question of the clock, and
all three give confidently wrong answers if two files disagree about what time
it is.

Three separate problems, handled in order:

1. **Timezone.** A line with a UTC offset resolves itself. A naive line does
   not, and the file's declared timezone settles it. With no declaration, UTC
   is assumed and the entry is marked `time_source="assumed"` so the UI can
   say so.
2. **Missing timestamps.** Some lines carry none at all. The preceding entry's
   time is carried forward, marked `"carried"`, which preserves ordering
   without inventing precision.
3. **Skew.** Two hosts whose clocks differ produce a timeline that interleaves
   wrongly. Skew is *detected and reported* here, and corrected only from an
   explicit user offset — automatic correction needs shared anchors between
   files, and those come from correlation, which has not run yet. Guessing at
   it from timestamp ranges alone would silently shift a file that legitimately
   covers a different window.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ingest.entry import Entry

#: Files whose time ranges are further apart than this are flagged. Chosen to
#: sit well above ordinary NTP drift (milliseconds) and below a timezone
#: mistake (whole hours), so it catches the two cases worth reporting: an
#: unsynchronised host, and a file that simply covers a different incident.
SKEW_WARN_SECONDS = 120.0


@dataclass(slots=True)
class FileClock:
    """What we worked out about one file's sense of time."""

    file_id: str
    first: datetime | None = None
    last: datetime | None = None
    #: Entries whose timestamp had to be assumed or carried, not read.
    unresolved: int = 0
    #: The offset applied, from `SourceFile.offset_seconds`.
    applied_offset_seconds: int = 0
    #: Set when this file's window sits clear of every other file's.
    skew_warning: str | None = None


@dataclass(slots=True)
class NormalizeReport:
    clocks: dict[str, FileClock] = field(default_factory=dict)
    #: Entries with no usable timestamp anywhere in their file.
    undated: int = 0
    #: True when at least one pair of files looks unsynchronised.
    skew_suspected: bool = False


def reference_year(texts: list[str]) -> int:
    """Pick the year to assume for formats that omit one (RFC 3164).

    Read from a four-digit year in any other file in the batch, so a syslog
    file uploaded alongside a dated one inherits the right year. Falls back to
    the current year — the only remaining option, and correct for the common
    case of logs pulled during an incident.
    """
    for text in texts:
        for line in text.splitlines()[:200]:
            stripped = line.lstrip()
            candidate = stripped[:4]
            if candidate.isdigit():
                year = int(candidate)
                if 2000 <= year <= 2100:
                    return year
    return datetime.now(tz=timezone.utc).year


def _zone(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def resolve_file(
    entries: list[Entry],
    *,
    file_id: str,
    tz_name: str | None,
    offset_seconds: int = 0,
) -> FileClock:
    """Resolve one file's entries to UTC, in place.

    Runs per file because the timezone and the carry-forward chain are
    per-file properties: carrying a timestamp forward across a file boundary
    would attribute one service's clock to another.
    """
    clock = FileClock(file_id=file_id, applied_offset_seconds=offset_seconds)
    zone = _zone(tz_name)
    shift = timedelta(seconds=offset_seconds) if offset_seconds else None
    carried: datetime | None = None

    for entry in entries:
        stamp = entry.timestamp

        if stamp is None:
            # No time on the line. Inherit the previous entry's so ordering
            # survives, and mark it as inherited so nothing treats it as read.
            if carried is not None:
                entry.timestamp = carried
                entry.time_source = "carried"
                clock.unresolved += 1
            else:
                entry.time_source = "none"
            continue

        if stamp.tzinfo is None:
            if zone is not None:
                stamp = stamp.replace(tzinfo=zone)
                entry.time_source = "zone"
            else:
                stamp = stamp.replace(tzinfo=timezone.utc)
                entry.time_source = "assumed"
                clock.unresolved += 1
        else:
            entry.time_source = "offset"

        stamp = stamp.astimezone(timezone.utc)
        if shift is not None:
            stamp += shift

        entry.timestamp = stamp
        carried = stamp

        if clock.first is None or stamp < clock.first:
            clock.first = stamp
        if clock.last is None or stamp > clock.last:
            clock.last = stamp

    # Entries before the first timestamped line got nothing to carry. Walk back
    # and give them the file's earliest time so they sort at its head rather
    # than falling out of the timeline entirely.
    if clock.first is not None:
        for entry in entries:
            if entry.timestamp is not None:
                break
            entry.timestamp = clock.first
            entry.time_source = "carried"
            clock.unresolved += 1

    return clock


def detect_skew(clocks: list[FileClock]) -> bool:
    """Flag files whose time window sits clear of every other file's.

    Deliberately conservative and deliberately non-corrective. Two files that
    genuinely cover different windows look identical to two files whose hosts
    disagree about the time, and only shared correlation anchors tell them
    apart. So this reports, and lets the user supply an offset.
    """
    dated = [c for c in clocks if c.first is not None and c.last is not None]
    if len(dated) < 2:
        return False

    suspected = False
    for clock in dated:
        gaps = [
            max(
                (other.first - clock.last).total_seconds(),
                (clock.first - other.last).total_seconds(),
            )
            for other in dated
            if other is not clock
        ]
        # The *smallest* gap to any other file: overlapping with even one file
        # means this one is anchored to the batch.
        nearest = min(gaps)
        if nearest > SKEW_WARN_SECONDS:
            suspected = True
            clock.skew_warning = (
                f"no overlap with the other files — nearest is {nearest / 60:.1f} min away. "
                "Either this covers a different window, or its host's clock is off; "
                "set an offset on the file if it is the clock."
            )
    return suspected


def sort_entries(entries: list[Entry]) -> None:
    """Order the batch by time, in place.

    Undated entries sort to the front rather than being dropped. `sorted` is
    stable, so entries sharing a timestamp — which is every entry in a file
    logging at second resolution — keep their original file order, and a
    burst of same-second lines still reads in the sequence it was written.
    """
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    entries.sort(key=lambda e: (e.timestamp or epoch, e.file_id, e.line_no))


__all__ = [
    "FileClock",
    "NormalizeReport",
    "SKEW_WARN_SECONDS",
    "detect_skew",
    "reference_year",
    "resolve_file",
    "sort_entries",
]
