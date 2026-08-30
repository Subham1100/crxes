"""In-house log parser.

Stage 1 of the pipeline that replaces the Log Parser agent. Raw logs of any
supported shape go in; a columnar `RecordBatch` in the OpenTelemetry log data
model comes out, and nothing downstream needs to know where it came from.

    from core.parser import ingest_file, Timings

    timings = Timings("ingest 1M")
    batch = ingest_file("app.log", timings=timings)
    print(timings.report())

Stages still to come, in order: mask, cluster (Drain), bucket, matrix.
"""

from core.parser.ingest import (
    UNKNOWN_SOURCE,
    ingest_file,
    ingest_lines,
    ingest_text,
    sniff,
)
from core.parser.model import (
    SEV_DEBUG,
    SEV_ERROR,
    SEV_FATAL,
    SEV_INFO,
    SEV_TRACE,
    SEV_UNSPECIFIED,
    SEV_WARN,
    SEVERITY_BY_NAME,
    IngestStats,
    LogRecord,
    RecordBatch,
    severity_text,
)
from core.parser.timing import Timings, peak_rss_bytes

__all__ = [
    "IngestStats",
    "LogRecord",
    "RecordBatch",
    "SEVERITY_BY_NAME",
    "SEV_DEBUG",
    "SEV_ERROR",
    "SEV_FATAL",
    "SEV_INFO",
    "SEV_TRACE",
    "SEV_UNSPECIFIED",
    "SEV_WARN",
    "Timings",
    "UNKNOWN_SOURCE",
    "ingest_file",
    "ingest_lines",
    "ingest_text",
    "peak_rss_bytes",
    "severity_text",
    "sniff",
]
