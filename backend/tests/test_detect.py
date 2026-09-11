"""Stage 2 — format detection.

Detection is a guess, so these assert the guess is right on realistic samples
and, just as importantly, that ambiguous input falls back to plain rather than
confidently picking wrong.
"""

import pytest

from ingest.detect import detect

PLAIN = """2026-08-04T14:58:11.004Z INFO  [api.gateway] request complete path=/checkout status=200
2026-08-04T14:58:21.113Z ERROR [db.pool] connection acquire timeout after 5000ms
    at Pool.acquire (/app/node_modules/pg-pool/index.js:212)
"""

GCP = """{"insertId":"a1","timestamp":"2026-08-04T14:58:11.004Z","severity":"ERROR","textPayload":"boom","logging.googleapis.com/trace":"projects/p/traces/4bf92f3577b34da6a3ce929d0e0e4736","resource":{"type":"cloud_run_revision","labels":{"service_name":"checkout"}}}
{"insertId":"a2","timestamp":"2026-08-04T14:58:12.004Z","severity":"INFO","textPayload":"ok","resource":{"type":"cloud_run_revision","labels":{"service_name":"checkout"}}}
"""

OTLP_FLAT = """{"Timestamp":"2026-08-04T14:58:11.004Z","SeverityText":"ERROR","SeverityNumber":17,"Body":"pool exhausted","TraceId":"4bf92f3577b34da6a3ce929d0e0e4736","SpanId":"00f067aa0ba902b7"}
"""

DATADOG = """{"timestamp":1785855491004,"status":"error","message":"boom","service":"checkout","dd.trace_id":"7277407061850445509","dd.span_id":"1234567890"}
"""

LOGFMT = """ts=2026-08-04T14:58:11.004Z level=info msg="request complete" path=/checkout status=200
ts=2026-08-04T14:58:21.113Z level=error msg="acquire timeout" pool=20 waiters=44
"""

CLF = """10.0.0.4 - - [04/Aug/2026:14:58:11 +0000] "GET /checkout HTTP/1.1" 200 4213 "-" "curl/8.4.0"
10.0.0.9 - - [04/Aug/2026:14:58:21 +0000] "POST /checkout HTTP/1.1" 503 108 "-" "curl/8.4.0"
"""

SYSLOG_3164 = """Aug  4 14:58:11 web-01 nginx[1234]: upstream timed out
Aug  4 14:58:12 web-01 sshd[991]: Accepted publickey for deploy
"""

SYSLOG_5424 = """<134>1 2026-08-04T14:58:11.004Z web-01 checkout 1234 ID47 [meta request_id="abc123"] request complete
"""

POSTGRES = """2026-08-04 14:58:11.004 UTC [1234] app@shop LOG:  duration: 142.113 ms
2026-08-04 14:58:21.113 UTC [1234] app@shop ERROR:  canceling statement due to statement timeout
2026-08-04 14:58:21.113 UTC [1234] app@shop STATEMENT:  SELECT * FROM orders WHERE id = 8831
"""

CRI = """2026-08-04T14:58:11.004Z stdout F request complete path=/checkout
2026-08-04T14:58:21.113Z stderr F ERROR pool exhausted
"""


@pytest.mark.parametrize(
    "text,expected",
    [
        (PLAIN, "plain"),
        (GCP, "json"),
        (OTLP_FLAT, "json"),
        (DATADOG, "json"),
        (LOGFMT, "logfmt"),
        (CLF, "clf"),
        (SYSLOG_3164, "syslog"),
        (SYSLOG_5424, "syslog5424"),
        (POSTGRES, "postgres"),
        (CRI, "cri"),
    ],
)
def test_detects_format(text: str, expected: str) -> None:
    assert detect(text).format == expected


@pytest.mark.parametrize(
    "text,expected",
    [(GCP, "gcp"), (OTLP_FLAT, "otlp"), (DATADOG, "datadog")],
)
def test_detects_json_flavor(text: str, expected: str) -> None:
    assert detect(text).flavor == expected


def test_override_wins_over_detection() -> None:
    result = detect(GCP, override="plain")
    assert result.format == "plain"
    assert result.confidence == 1.0


def test_unknown_override_rejected() -> None:
    with pytest.raises(ValueError, match="unknown format"):
        detect(PLAIN, override="parquet")


def test_empty_input_is_plain() -> None:
    assert detect("").format == "plain"


def test_ambiguous_input_falls_back_to_plain() -> None:
    # Mentions `status=200` but is not logfmt — one pair is not three.
    assert detect("the server returned status=200 eventually\n").format == "plain"


def test_confidence_reflects_a_mixed_file() -> None:
    mixed = GCP + PLAIN
    result = detect(mixed)
    assert result.format == "json"
    assert 0.3 < result.confidence < 1.0
