"""Phase A end to end — stages 1 through 5 over a realistic multi-file batch."""

import pytest

from ingest import (
    MAX_FILES,
    RedactionPolicy,
    SourceFile,
    TooManyFiles,
    ingest,
    ingest_text,
)
from tests.test_detect import CLF, GCP, PLAIN, POSTGRES

GATEWAY = """10.0.0.4 - - [04/Aug/2026:14:58:11 +0000] "GET /checkout HTTP/1.1" 200 4213 "-" "curl/8.4.0" request_id=r-8831
10.0.0.4 - - [04/Aug/2026:14:58:21 +0000] "POST /checkout HTTP/1.1" 503 108 "-" "curl/8.4.0" request_id=r-8832
"""

API = """2026-08-04T14:58:11.004Z INFO  [api] request complete request_id=r-8831 order=8831
2026-08-04T14:58:21.115Z ERROR [api] request failed request_id=r-8832 order=8832
    at CheckoutService.reserve (/app/src/checkout.ts:88)
"""

DB = """2026-08-04 14:58:11.004 UTC [1234] app@shop LOG:  duration: 142.113 ms
2026-08-04 14:58:21.113 UTC [1234] app@shop ERROR:  canceling statement due to statement timeout
2026-08-04 14:58:21.113 UTC [1234] app@shop STATEMENT:  SELECT * FROM orders WHERE id = 8832
"""


def batch() -> list[SourceFile]:
    return [
        SourceFile(name="nginx.log", content=GATEWAY, service="edge", role="proxy"),
        SourceFile(name="api.log", content=API, service="api", role="backend"),
        SourceFile(name="postgres.log", content=DB, service="shop-db", role="db"),
    ]


# --- multi-file batch --------------------------------------------------------


def test_each_file_is_detected_independently() -> None:
    result = ingest(batch())
    assert [f.format for f in result.files] == ["clf", "plain", "postgres"]


def test_file_labels_reach_every_entry() -> None:
    result = ingest(batch())
    by_service = {e.service for e in result.entries}
    assert by_service == {"edge", "api", "shop-db"}
    assert {e.role for e in result.entries} == {"proxy", "backend", "db"}


def test_entries_are_merged_into_one_timeline() -> None:
    result = ingest(batch())
    stamps = [e.timestamp for e in result.entries]
    assert stamps == sorted(stamps)
    # Interleaved, not concatenated: the batch is one timeline, not three.
    assert len({e.service for e in result.entries[:3]}) > 1


def test_join_keys_survive_the_whole_phase() -> None:
    """The point of the phase: what correlation will actually join on."""
    result = ingest(batch())
    request_ids = {
        e.correlation_keys["request_id"]
        for e in result.entries
        if "request_id" in e.correlation_keys
    }
    assert request_ids == {"r-8831", "r-8832"}
    pids = {e.correlation_keys["pg_pid"] for e in result.entries if "pg_pid" in e.correlation_keys}
    assert pids == {"1234"}


def test_per_file_report_counts_correlation_coverage() -> None:
    result = ingest(batch())
    reports = {f.service: f for f in result.files}
    assert reports["edge"].with_correlation_key == 2
    assert reports["shop-db"].with_correlation_key == 2
    assert reports["edge"].entry_count == 2
    # The stack frame folded into its error, so three lines are two entries.
    assert reports["api"].entry_count == 2


def test_file_ids_are_unique_and_stable() -> None:
    result = ingest(batch())
    assert [f.file_id for f in result.files] == ["f0", "f1", "f2"]
    assert {e.file_id for e in result.entries} == {"f0", "f1", "f2"}


def test_time_range_spans_the_batch() -> None:
    first, last = ingest(batch()).time_range()
    assert first is not None and last is not None
    assert first < last


def test_trace_coverage_is_zero_without_trace_ids() -> None:
    assert ingest(batch()).trace_coverage == 0.0


def test_trace_coverage_is_one_when_every_entry_has_a_trace() -> None:
    result = ingest([SourceFile(name="gcp.json", content=GCP, service="checkout")])
    assert result.trace_coverage == pytest.approx(0.5)  # one of two records has a trace


# --- timezone and skew -------------------------------------------------------


def test_declared_timezone_resolves_a_naive_file() -> None:
    result = ingest(
        [SourceFile(name="postgres.log", content=DB, service="db", timezone="America/New_York")]
    )
    assert all(e.time_source == "zone" for e in result.entries)
    assert result.entries[0].timestamp.hour == 18  # 14:58 EDT → 18:58 UTC


def test_skew_is_reported_not_corrected() -> None:
    shifted = API.replace("2026-08-04T14:58", "2026-08-04T19:58")
    result = ingest(
        [
            SourceFile(name="a.log", content=GATEWAY, service="edge", role="proxy"),
            SourceFile(name="b.log", content=shifted, service="api", role="backend"),
        ]
    )
    assert result.skew_suspected is True
    assert any(f.skew_warning for f in result.files)
    # Reported only — the timestamps are untouched, because correcting them
    # needs anchors that correlation has not produced yet.
    api_entries = [e for e in result.entries if e.service == "api"]
    assert api_entries[0].timestamp.hour == 19


def test_an_explicit_offset_is_applied_and_reported() -> None:
    shifted = API.replace("2026-08-04T14:58", "2026-08-04T19:58")
    result = ingest(
        [
            SourceFile(name="a.log", content=GATEWAY, service="edge", role="proxy"),
            SourceFile(
                name="b.log", content=shifted, service="api", role="backend", offset_seconds=-18000
            ),
        ]
    )
    assert result.skew_suspected is False
    api_entries = [e for e in result.entries if e.service == "api"]
    assert api_entries[0].timestamp.hour == 14
    assert result.files[1].applied_offset_seconds == -18000


def test_syslog_borrows_its_year_from_a_dated_sibling() -> None:
    result = ingest(
        [
            SourceFile(name="app.log", content="2019-08-04T14:58:11Z INFO hi", service="app"),
            SourceFile(
                name="sys.log", content="Aug  4 14:58:11 web-01 nginx[1]: hi", service="sys"
            ),
        ]
    )
    sys_entries = [e for e in result.entries if e.service == "sys"]
    assert sys_entries[0].timestamp.year == 2019


# --- redaction ---------------------------------------------------------------


def test_redaction_runs_over_the_batch_and_reports() -> None:
    leaky = SourceFile(
        name="app.log",
        content="2026-08-04T14:58:11Z INFO [api] connecting password=hunter2 user=app\n",
        service="api",
    )
    result = ingest([leaky])
    assert "hunter2" not in result.entries[0].message
    assert "hunter2" not in result.entries[0].raw
    assert result.redaction.entries_affected == 1


def test_redaction_can_be_turned_off() -> None:
    leaky = SourceFile(
        name="app.log",
        content="2026-08-04T14:58:11Z INFO [api] password=hunter2\n",
        service="api",
    )
    result = ingest([leaky], policy=RedactionPolicy(enabled=False))
    assert "hunter2" in result.entries[0].message


# --- limits ------------------------------------------------------------------


def test_line_cap_truncates_and_reports_rather_than_dropping_silently() -> None:
    many = "\n".join(f"2026-08-04T14:58:{i % 60:02d}Z INFO [api] line {i}" for i in range(50))
    result = ingest([SourceFile(name="a.log", content=many, service="api")], max_lines=10)
    assert result.entry_count == 10
    assert result.dropped_lines == 40
    assert result.files[0].dropped_lines == 40


def test_the_line_budget_is_shared_across_files() -> None:
    many = "\n".join(f"2026-08-04T14:58:{i % 60:02d}Z INFO [api] line {i}" for i in range(20))
    result = ingest(
        [
            SourceFile(name="a.log", content=many, service="a"),
            SourceFile(name="b.log", content=many, service="b"),
        ],
        max_lines=25,
    )
    assert result.entry_count == 25
    assert result.files[0].dropped_lines == 0
    assert result.files[1].dropped_lines == 15


def test_byte_cap_truncates_the_content() -> None:
    result = ingest(
        [SourceFile(name="a.log", content="x" * 5000, service="a")],
        max_bytes=100,
    )
    assert result.files[0].bytes <= 100


def test_too_many_files_is_rejected() -> None:
    files = [SourceFile(name=f"{i}.log", content="hi") for i in range(MAX_FILES + 1)]
    with pytest.raises(TooManyFiles):
        ingest(files)


def test_empty_batch_is_not_an_error() -> None:
    result = ingest([])
    assert result.entry_count == 0
    assert result.trace_coverage == 0.0
    assert result.time_range() == (None, None)


def test_empty_file_produces_no_entries() -> None:
    result = ingest([SourceFile(name="a.log", content="")])
    assert result.entry_count == 0


# --- paste path --------------------------------------------------------------


def test_paste_reads_services_from_the_lines_themselves() -> None:
    result = ingest_text(PLAIN)
    assert {e.service for e in result.entries} == {"api.gateway", "db.pool"}
    assert result.files[0].service == "pasted"


def test_paste_detects_a_non_plain_format_too() -> None:
    result = ingest_text(CLF)
    assert result.files[0].format == "clf"


def test_serialization_round_trips_every_field() -> None:
    result = ingest(batch())
    rows = result.to_dicts()
    assert len(rows) == result.entry_count
    expected = {
        "file_id",
        "line_no",
        "service",
        "role",
        "timestamp",
        "time_source",
        "level",
        "message",
        "trace_id",
        "span_id",
        "parent_span_id",
        "attributes",
        "correlation_keys",
        "raw",
        "template_id",
        "flow_id",
    }
    assert set(rows[0]) == expected


def test_result_is_json_serializable() -> None:
    import json

    json.dumps(ingest(batch() + [SourceFile("pg.log", POSTGRES, "db")]).to_dicts())
