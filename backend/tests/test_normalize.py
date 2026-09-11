"""Stage 4 — timezone resolution, carry-forward, skew detection."""

from datetime import datetime, timedelta, timezone

from ingest.entry import Entry
from ingest.normalize import (
    SKEW_WARN_SECONDS,
    detect_skew,
    reference_year,
    resolve_file,
    sort_entries,
)


def entry(stamp: datetime | None, line_no: int = 1, file_id: str = "f0") -> Entry:
    return Entry(
        file_id=file_id,
        line_no=line_no,
        service="svc",
        role="backend",
        timestamp=stamp,
        time_source="naive" if stamp and stamp.tzinfo is None else "offset",
        level="info",
        message="m",
        raw="m",
    )


def test_naive_timestamps_resolve_against_the_declared_zone() -> None:
    entries = [entry(datetime(2026, 8, 4, 14, 58, 11))]
    resolve_file(entries, file_id="f0", tz_name="America/New_York")
    # August is EDT, UTC-4.
    assert entries[0].timestamp == datetime(2026, 8, 4, 18, 58, 11, tzinfo=timezone.utc)
    assert entries[0].time_source == "zone"


def test_naive_timestamps_without_a_zone_are_assumed_utc_and_flagged() -> None:
    entries = [entry(datetime(2026, 8, 4, 14, 58, 11))]
    clock = resolve_file(entries, file_id="f0", tz_name=None)
    assert entries[0].timestamp == datetime(2026, 8, 4, 14, 58, 11, tzinfo=timezone.utc)
    assert entries[0].time_source == "assumed"
    assert clock.unresolved == 1


def test_aware_timestamps_are_converted_not_reinterpreted() -> None:
    stamp = datetime(2026, 8, 4, 14, 58, 11, tzinfo=timezone(timedelta(hours=2)))
    entries = [entry(stamp)]
    resolve_file(entries, file_id="f0", tz_name="America/New_York")
    assert entries[0].timestamp == datetime(2026, 8, 4, 12, 58, 11, tzinfo=timezone.utc)
    assert entries[0].time_source == "offset"


def test_an_unknown_zone_name_falls_back_to_utc() -> None:
    entries = [entry(datetime(2026, 8, 4, 14, 58, 11))]
    resolve_file(entries, file_id="f0", tz_name="Mars/Olympus")
    assert entries[0].time_source == "assumed"


def test_missing_timestamps_carry_forward() -> None:
    entries = [entry(datetime(2026, 8, 4, 14, 0, 0), 1), entry(None, 2)]
    resolve_file(entries, file_id="f0", tz_name=None)
    assert entries[1].timestamp == entries[0].timestamp
    assert entries[1].time_source == "carried"


def test_entries_before_the_first_timestamp_carry_backward() -> None:
    entries = [entry(None, 1), entry(datetime(2026, 8, 4, 14, 0, 0), 2)]
    resolve_file(entries, file_id="f0", tz_name=None)
    assert entries[0].timestamp == entries[1].timestamp
    assert entries[0].time_source == "carried"


def test_a_file_with_no_timestamps_at_all_stays_undated() -> None:
    entries = [entry(None, 1), entry(None, 2)]
    clock = resolve_file(entries, file_id="f0", tz_name=None)
    assert clock.first is None
    assert all(e.timestamp is None and e.time_source == "none" for e in entries)


def test_explicit_offset_is_applied() -> None:
    entries = [entry(datetime(2026, 8, 4, 14, 0, 0, tzinfo=timezone.utc))]
    clock = resolve_file(entries, file_id="f0", tz_name=None, offset_seconds=-90)
    assert entries[0].timestamp == datetime(2026, 8, 4, 13, 58, 30, tzinfo=timezone.utc)
    assert clock.applied_offset_seconds == -90


def test_clock_records_the_file_time_range() -> None:
    entries = [
        entry(datetime(2026, 8, 4, 14, 5, 0, tzinfo=timezone.utc), 1),
        entry(datetime(2026, 8, 4, 14, 0, 0, tzinfo=timezone.utc), 2),
    ]
    clock = resolve_file(entries, file_id="f0", tz_name=None)
    assert clock.first == datetime(2026, 8, 4, 14, 0, 0, tzinfo=timezone.utc)
    assert clock.last == datetime(2026, 8, 4, 14, 5, 0, tzinfo=timezone.utc)


# --- skew --------------------------------------------------------------------


def _clock(file_id: str, start_minute: int, span_minutes: int = 5):
    base = datetime(2026, 8, 4, 14, 0, 0, tzinfo=timezone.utc)
    entries = [
        entry(base + timedelta(minutes=start_minute), 1, file_id),
        entry(base + timedelta(minutes=start_minute + span_minutes), 2, file_id),
    ]
    return resolve_file(entries, file_id=file_id, tz_name=None)


def test_overlapping_files_raise_no_skew_warning() -> None:
    clocks = [_clock("f0", 0), _clock("f1", 2)]
    assert detect_skew(clocks) is False
    assert all(c.skew_warning is None for c in clocks)


def test_a_file_with_no_overlap_is_flagged() -> None:
    clocks = [_clock("f0", 0), _clock("f1", 60)]
    assert detect_skew(clocks) is True
    assert all(c.skew_warning is not None for c in clocks)


def test_skew_threshold_is_respected() -> None:
    # Gap just under the threshold — within ordinary drift, not reported.
    gap = (SKEW_WARN_SECONDS - 30) / 60
    clocks = [_clock("f0", 0, span_minutes=1), _clock("f1", 1 + gap, span_minutes=1)]
    assert detect_skew(clocks) is False


def test_a_single_file_is_never_skewed() -> None:
    assert detect_skew([_clock("f0", 0)]) is False


def test_a_file_overlapping_only_one_of_three_is_not_flagged() -> None:
    """Anchoring to any one file in the batch is enough."""
    clocks = [_clock("f0", 0), _clock("f1", 2), _clock("f2", 600)]
    detect_skew(clocks)
    assert clocks[0].skew_warning is None
    assert clocks[1].skew_warning is None
    assert clocks[2].skew_warning is not None


# --- ordering ----------------------------------------------------------------


def test_sort_is_stable_within_a_timestamp() -> None:
    stamp = datetime(2026, 8, 4, 14, 0, 0, tzinfo=timezone.utc)
    entries = [entry(stamp, 3), entry(stamp, 1), entry(stamp, 2)]
    sort_entries(entries)
    assert [e.line_no for e in entries] == [1, 2, 3]


def test_undated_entries_sort_to_the_front_rather_than_vanishing() -> None:
    entries = [entry(datetime(2026, 8, 4, 14, 0, 0, tzinfo=timezone.utc), 1), entry(None, 2)]
    sort_entries(entries)
    assert entries[0].timestamp is None
    assert len(entries) == 2


# --- reference year ----------------------------------------------------------


def test_reference_year_is_borrowed_from_a_dated_file() -> None:
    assert reference_year(["Aug  4 14:58:11 host x: y", "2019-08-04T14:58:11Z INFO hi"]) == 2019


def test_reference_year_falls_back_to_now() -> None:
    year = reference_year(["Aug  4 14:58:11 host x: y"])
    assert year == datetime.now(tz=timezone.utc).year


def test_implausible_years_are_ignored() -> None:
    assert reference_year(["1234567890 something"]) == datetime.now(tz=timezone.utc).year
