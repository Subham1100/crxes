"""Stage 3 — the parsers.

Each test asserts the things later phases actually depend on: that trace
context survives when it exists, that join keys are extracted, that a stack
trace stays attached to its error, and that no line is ever silently lost.
"""

import pytest

from ingest.entry import Entry
from ingest.parsers import ParseContext, parse
from tests.test_detect import (
    CLF,
    CRI,
    DATADOG,
    GCP,
    LOGFMT,
    OTLP_FLAT,
    PLAIN,
    POSTGRES,
    SYSLOG_3164,
    SYSLOG_5424,
)


def ctx(**kwargs) -> ParseContext:
    base = {
        "file_id": "f0",
        "service": "svc",
        "role": "backend",
        "service_declared": False,
        "reference_year": 2026,
        "flavor": None,
    }
    return ParseContext(**{**base, **kwargs})


def run(text: str, fmt: str, **kwargs) -> list[Entry]:
    return list(parse(text, fmt, ctx(**kwargs)))


# --- plain -------------------------------------------------------------------


def test_plain_folds_stack_trace_into_its_error() -> None:
    entries = run(PLAIN, "plain")
    assert len(entries) == 2
    assert entries[1].level == "error"
    assert "at Pool.acquire" in entries[1].message


def test_plain_reads_service_from_bracketed_logger_when_undeclared() -> None:
    entries = run(PLAIN, "plain")
    assert [e.service for e in entries] == ["api.gateway", "db.pool"]


def test_declared_service_wins_and_logger_is_kept() -> None:
    entries = run(PLAIN, "plain", service="checkout", service_declared=True)
    assert entries[0].service == "checkout"
    assert entries[0].attributes["logger"] == "api.gateway"


def test_plain_parses_timestamp_and_level() -> None:
    entry = run(PLAIN, "plain")[0]
    assert entry.timestamp is not None
    assert entry.timestamp.hour == 14
    assert entry.level == "info"


def test_level_word_is_stripped_from_the_message() -> None:
    entry = run(PLAIN, "plain")[1]
    assert not entry.message.startswith("ERROR")


# --- json --------------------------------------------------------------------


def test_gcp_trace_prefix_is_stripped() -> None:
    entries = run(GCP, "json", flavor="gcp")
    assert entries[0].trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"


def test_gcp_service_comes_from_resource_labels() -> None:
    entries = run(GCP, "json", flavor="gcp")
    assert entries[0].service == "checkout"
    assert entries[0].level == "error"
    assert entries[0].message == "boom"


def test_gcp_insert_id_is_a_correlation_key() -> None:
    entries = run(GCP, "json", flavor="gcp")
    assert entries[0].correlation_keys["insertId"] == "a1"


def test_otlp_flat_reads_trace_and_span() -> None:
    entry = run(OTLP_FLAT, "json", flavor="otlp")[0]
    assert entry.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert entry.span_id == "00f067aa0ba902b7"
    assert entry.level == "error"
    assert entry.message == "pool exhausted"


def test_datadog_millisecond_epoch_and_decimal_ids() -> None:
    entry = run(DATADOG, "json", flavor="datadog")[0]
    assert entry.timestamp is not None
    assert entry.timestamp.year == 2026
    # Datadog IDs are decimal, not hex, and are kept verbatim as join keys.
    assert entry.trace_id == "7277407061850445509"
    assert entry.level == "error"


def test_all_zero_trace_id_is_rejected() -> None:
    text = '{"timestamp":"2026-08-04T14:58:11Z","message":"x","trace_id":"00000000000000000000000000000000"}\n'
    assert run(text, "json")[0].trace_id is None


def test_unmapped_json_keys_are_kept_as_attributes() -> None:
    text = '{"timestamp":"2026-08-04T14:58:11Z","message":"x","wait_queue":44,"pool":"20/20"}\n'
    entry = run(text, "json")[0]
    assert entry.attributes["wait_queue"] == 44
    assert entry.attributes["pool"] == "20/20"


def test_json_parser_tolerates_interleaved_plain_text() -> None:
    text = GCP + "not json at all\n"
    entries = run(text, "json", flavor="gcp")
    assert len(entries) == 3
    assert entries[2].message == "not json at all"


def test_record_without_a_message_field_keeps_the_document() -> None:
    entry = run('{"foo":"bar"}\n', "json")[0]
    assert "foo" in entry.message


def test_otlp_envelope_is_flattened() -> None:
    envelope = (
        '{"resourceLogs":[{"resource":{"attributes":[{"key":"service.name",'
        '"value":{"stringValue":"checkout"}}]},"scopeLogs":[{"scope":{"name":"app"},'
        '"logRecords":['
        '{"timeUnixNano":"1785855491004000000","severityNumber":17,"severityText":"ERROR",'
        '"body":{"stringValue":"pool exhausted"},"traceId":"4bf92f3577b34da6a3ce929d0e0e4736",'
        '"spanId":"00f067aa0ba902b7","attributes":[{"key":"wait_queue","value":{"intValue":"44"}}]},'
        '{"timeUnixNano":"1785855492004000000","severityNumber":9,"severityText":"INFO",'
        '"body":{"stringValue":"recovered"}}'
        "]}]}]}\n"
    )
    entries = run(envelope, "json", flavor="otlp")
    assert len(entries) == 2
    assert entries[0].service == "checkout"
    assert entries[0].level == "error"
    assert entries[0].message == "pool exhausted"
    assert entries[0].trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert entries[0].attributes["wait_queue"] == 44
    assert entries[0].timestamp is not None and entries[0].timestamp.year == 2026
    assert entries[1].level == "info"


def test_docker_envelope_is_unwrapped() -> None:
    text = '{"log":"2026-08-04T14:58:21Z ERROR [db.pool] boom\\n","stream":"stderr","time":"2026-08-04T14:58:21.113Z"}\n'
    entry = run(text, "json", flavor="docker")[0]
    assert entry.level == "error"
    assert entry.attributes["stream"] == "stderr"
    assert "boom" in entry.message


# --- logfmt ------------------------------------------------------------------


def test_logfmt_maps_known_keys_and_keeps_the_rest() -> None:
    entries = run(LOGFMT, "logfmt")
    assert entries[0].message == "request complete"
    assert entries[0].level == "info"
    assert entries[0].attributes["status"] == 200
    assert entries[1].level == "error"
    assert entries[1].attributes["waiters"] == 44


def test_logfmt_trace_id_is_not_duplicated_into_attributes() -> None:
    text = 'ts=2026-08-04T14:58:11Z level=info msg=hi trace_id=abc123 span_id=def456\n'
    entry = run(text, "logfmt")[0]
    assert entry.trace_id == "abc123"
    assert entry.span_id == "def456"
    assert "trace_id" not in entry.attributes
    assert "span_id" not in entry.attributes


def test_logfmt_extracts_request_id_as_a_join_key() -> None:
    text = 'ts=2026-08-04T14:58:11Z level=info msg=hi request_id=r-99\n'
    assert run(text, "logfmt")[0].correlation_keys["request_id"] == "r-99"


# --- clf ---------------------------------------------------------------------


def test_clf_derives_level_from_status() -> None:
    entries = run(CLF, "clf")
    assert [e.level for e in entries] == ["info", "error"]


def test_clf_extracts_http_fields() -> None:
    entry = run(CLF, "clf")[0]
    assert entry.attributes["http.method"] == "GET"
    assert entry.attributes["http.path"] == "/checkout"
    assert entry.attributes["http.status"] == 200
    assert entry.attributes["client.ip"] == "10.0.0.4"
    assert entry.timestamp is not None


def test_clf_picks_up_an_appended_request_id() -> None:
    line = (
        '10.0.0.4 - - [04/Aug/2026:14:58:11 +0000] "GET /c HTTP/1.1" 200 12 "-" "curl" '
        "request_id=abc-123\n"
    )
    assert run(line, "clf")[0].correlation_keys["request_id"] == "abc-123"


def test_clf_falls_back_for_a_non_access_line() -> None:
    text = CLF + "2026/08/04 14:58:30 [error] 42#42: upstream timed out\n"
    entries = run(text, "clf")
    assert len(entries) == 3
    assert "upstream timed out" in entries[2].message


# --- syslog ------------------------------------------------------------------


def test_syslog_3164_reads_tag_pid_and_priority() -> None:
    entries = run(SYSLOG_3164, "syslog")
    assert len(entries) == 2
    assert entries[0].service == "nginx"
    assert entries[0].correlation_keys["pid"] == "1234"
    assert entries[0].message == "upstream timed out"


def test_syslog_3164_timestamp_takes_the_reference_year_and_stays_naive() -> None:
    entry = run(SYSLOG_3164, "syslog")[0]
    assert entry.timestamp is not None
    assert entry.timestamp.year == 2026
    # No zone in RFC 3164 — normalize resolves it, not the parser.
    assert entry.timestamp.tzinfo is None
    assert entry.time_source == "naive"


def test_syslog_5424_reads_structured_data() -> None:
    entry = run(SYSLOG_5424, "syslog5424")[0]
    assert entry.service == "checkout"
    assert entry.level == "info"  # <134> → facility 16, severity 6
    assert entry.message == "request complete"
    assert entry.correlation_keys["request_id"] == "abc123"
    assert entry.correlation_keys["pid"] == "1234"


# --- postgres ----------------------------------------------------------------


def test_postgres_extracts_the_backend_pid() -> None:
    entries = run(POSTGRES, "postgres")
    assert all(e.correlation_keys["pg_pid"] == "1234" for e in entries)


def test_postgres_folds_statement_into_its_error() -> None:
    entries = run(POSTGRES, "postgres")
    assert len(entries) == 2
    assert entries[1].level == "error"
    assert "SELECT * FROM orders" in entries[1].message


def test_postgres_reads_duration_and_user_db() -> None:
    entry = run(POSTGRES, "postgres")[0]
    assert entry.attributes["duration_ms"] == 142.113
    assert entry.attributes["db.user"] == "app"
    assert entry.attributes["db.name"] == "shop"


def test_postgres_zone_abbreviation_leaves_timestamp_naive() -> None:
    # "UTC" is unambiguous but "CST" is not, so no abbreviation is trusted;
    # the file's declared timezone settles it in normalize.
    assert run(POSTGRES, "postgres")[0].timestamp.tzinfo is None


# --- cri ---------------------------------------------------------------------


def test_cri_unwraps_the_envelope() -> None:
    entries = run(CRI, "cri")
    assert len(entries) == 2
    assert entries[0].message == "request complete path=/checkout"
    assert entries[0].attributes["stream"] == "stdout"
    assert entries[0].timestamp is not None


def test_cri_stderr_without_a_level_is_raised_to_warn() -> None:
    entries = run("2026-08-04T14:58:11.004Z stderr F something happened\n", "cri")
    assert entries[0].level == "warn"


def test_cri_stderr_does_not_downgrade_a_parsed_level() -> None:
    assert run(CRI, "cri")[1].level == "error"


def test_cri_rejoins_partial_fragments() -> None:
    text = (
        "2026-08-04T14:58:11.004Z stdout P ERROR pool exh\n"
        "2026-08-04T14:58:11.004Z stdout F austed after 5000ms\n"
    )
    entries = run(text, "cri")
    assert len(entries) == 1
    assert "pool exhausted after 5000ms" in entries[0].message


def test_cri_parses_a_json_payload_on_its_own_terms() -> None:
    text = (
        '2026-08-04T14:58:11.004Z stdout F {"level":"error","msg":"boom",'
        '"trace_id":"4bf92f3577b34da6a3ce929d0e0e4736"}\n'
    )
    entry = run(text, "cri")[0]
    assert entry.level == "error"
    assert entry.message == "boom"
    assert entry.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"


# --- invariants --------------------------------------------------------------


@pytest.mark.parametrize(
    "text,fmt",
    [
        (PLAIN, "plain"),
        (GCP, "json"),
        (LOGFMT, "logfmt"),
        (CLF, "clf"),
        (SYSLOG_3164, "syslog"),
        (SYSLOG_5424, "syslog5424"),
        (POSTGRES, "postgres"),
        (CRI, "cri"),
    ],
)
def test_every_parser_sets_the_required_fields(text: str, fmt: str) -> None:
    for entry in run(text, fmt):
        assert entry.file_id == "f0"
        assert entry.line_no >= 1
        assert entry.service
        assert entry.role == "backend"
        assert entry.level
        assert entry.raw


@pytest.mark.parametrize(
    "text,fmt",
    [(PLAIN, "plain"), (GCP, "json"), (LOGFMT, "logfmt"), (CLF, "clf"), (CRI, "cri")],
)
def test_no_parser_drops_a_line(text: str, fmt: str) -> None:
    """Continuations fold, but every non-blank line reaches some entry's raw."""
    joined = "\n".join(e.raw for e in run(text, fmt))
    for line in text.splitlines():
        if line.strip():
            assert line.strip() in joined


def test_garbage_still_produces_entries() -> None:
    entries = run("\x00\x01 not a log\n???\n", "plain")
    assert len(entries) == 2
