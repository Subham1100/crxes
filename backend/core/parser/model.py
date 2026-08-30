"""The record model everything downstream is built on.

Two decisions here shape the whole parser:

**The shape is OpenTelemetry's log data model.** Timestamp, SeverityNumber,
Body, Resource, Attributes, TraceId, SpanId. Pasted text, CloudWatch, GCP
Logging and OTLP all land in this one shape, so the stages after ingest never
learn where a record came from.

**Storage is columnar, not a list of objects.** A million `LogRecord` instances
would cost roughly a gigabyte and touch a million scattered heap allocations;
six parallel arrays cost a fraction of that and stay cache-friendly. It is also
the layout the bucketing stage wants — a matrix column is a slice of an array,
not a walk over objects — and the layout a C++ port would use anyway, so the
seam does not move when that happens.

`LogRecord` still exists as a read-only view onto row *i*, for the places where
one record at a time is genuinely what you want. It is never used in a hot loop.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass, field
from typing import Any, Iterator

# OpenTelemetry SeverityNumber. The spec allocates four numbers per band so a
# source can express "a slightly worse ERROR"; we emit the band floor and let
# the band, not the offset, carry the meaning.
SEV_UNSPECIFIED = 0
SEV_TRACE = 1
SEV_DEBUG = 5
SEV_INFO = 9
SEV_WARN = 13
SEV_ERROR = 17
SEV_FATAL = 21

#: Level tokens as they actually appear in logs, mapped onto the spec's scale.
#: Syslog orders severity the other way (crit is *worse* than err), which is
#: why CRITICAL lands in the FATAL band rather than beside ERROR.
SEVERITY_BY_NAME: dict[str, int] = {
    "TRACE": SEV_TRACE,
    "FINEST": SEV_TRACE,
    "VERBOSE": SEV_TRACE,
    "DEBUG": SEV_DEBUG,
    "FINE": SEV_DEBUG,
    "FINER": SEV_DEBUG,
    "INFO": SEV_INFO,
    "INFORMATION": SEV_INFO,
    "NOTICE": SEV_INFO + 1,
    "WARN": SEV_WARN,
    "WARNING": SEV_WARN,
    "ERROR": SEV_ERROR,
    "ERR": SEV_ERROR,
    "SEVERE": SEV_ERROR,
    "CRIT": SEV_FATAL,
    "CRITICAL": SEV_FATAL,
    "FATAL": SEV_FATAL,
    "ALERT": SEV_FATAL + 1,
    "EMERG": SEV_FATAL + 3,
    "EMERGENCY": SEV_FATAL + 3,
    "PANIC": SEV_FATAL + 3,
}

_BAND_NAMES = ("TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL")


def severity_text(number: int) -> str:
    """The band name for a SeverityNumber — `18` reads back as `ERROR`."""
    if number <= 0:
        return "UNSPECIFIED"
    band = (number - 1) // 4
    return _BAND_NAMES[band] if band < len(_BAND_NAMES) else "FATAL"


@dataclass(frozen=True, slots=True)
class LogRecord:
    """A read-only view onto one row of a `RecordBatch`."""

    timestamp: int  # unix nanoseconds; 0 when the source had none
    severity: int
    source: str
    body: str
    trace_id: str
    attributes: dict[str, Any] | None

    @property
    def severity_text(self) -> str:
        return severity_text(self.severity)


@dataclass(slots=True)
class IngestStats:
    """What the ingest pass saw, as opposed to what it produced.

    Kept separate from `Timings` because these numbers describe the *input* and
    belong in the digest; timings describe the machine and do not.
    """

    bytes_in: int = 0
    lines_total: int = 0
    lines_blank: int = 0
    lines_continuation: int = 0  # folded into the record above
    lines_unparsed: int = 0  # JSON that would not decode
    records: int = 0
    with_timestamp: int = 0
    with_severity: int = 0
    with_trace: int = 0
    format: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "bytes_in": self.bytes_in,
            "lines_total": self.lines_total,
            "lines_blank": self.lines_blank,
            "lines_continuation": self.lines_continuation,
            "lines_unparsed": self.lines_unparsed,
            "records": self.records,
            "with_timestamp": self.with_timestamp,
            "with_severity": self.with_severity,
            "with_trace": self.with_trace,
        }


class RecordBatch:
    """Log records in column form.

    `timestamps` and `severities` are typed arrays — 8 and 1 bytes per record,
    against ~28 for a boxed Python int. `source_ids` indexes into `sources`,
    because a million records carry maybe a dozen distinct service names and
    storing the string a million times is pure waste.

    `attributes` stays `None` unless ingest was asked to keep it. A dict per
    record is the single most expensive thing this class can hold, and nothing
    before the root-cause stage reads it.
    """

    __slots__ = (
        "timestamps",
        "severities",
        "source_ids",
        "bodies",
        "trace_ids",
        "attributes",
        "sources",
        "_source_index",
        "stats",
    )

    def __init__(self, keep_attributes: bool = False) -> None:
        self.timestamps: array[int] = array("q")  # int64 unix nanos
        self.severities: array[int] = array("B")  # OTel 0-24 fits a byte
        self.source_ids: array[int] = array("i")
        self.bodies: list[str] = []
        self.trace_ids: list[str] = []
        self.attributes: list[dict[str, Any]] | None = [] if keep_attributes else None
        self.sources: list[str] = []
        self._source_index: dict[str, int] = {}
        self.stats = IngestStats()

    def __len__(self) -> int:
        return len(self.bodies)

    def source_id(self, name: str) -> int:
        """Intern a service/logger name, returning its column value."""
        got = self._source_index.get(name)
        if got is None:
            got = len(self.sources)
            self._source_index[name] = got
            self.sources.append(name)
        return got

    def append(
        self,
        timestamp: int,
        severity: int,
        source: str,
        body: str,
        trace_id: str = "",
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """Convenience append. Ingest bypasses this and writes columns directly —
        a bound-method call per record is measurable at a million of them."""
        self.timestamps.append(timestamp)
        self.severities.append(severity)
        self.source_ids.append(self.source_id(source))
        self.bodies.append(body)
        self.trace_ids.append(trace_id)
        if self.attributes is not None:
            self.attributes.append(attributes or {})

    def record(self, i: int) -> LogRecord:
        return LogRecord(
            timestamp=self.timestamps[i],
            severity=self.severities[i],
            source=self.sources[self.source_ids[i]],
            body=self.bodies[i],
            trace_id=self.trace_ids[i],
            attributes=self.attributes[i] if self.attributes is not None else None,
        )

    def __iter__(self) -> Iterator[LogRecord]:
        return (self.record(i) for i in range(len(self)))

    def time_span(self) -> tuple[int, int]:
        """(first, last) non-zero timestamp in nanoseconds, or (0, 0).

        Not `min`/`max` over the whole column blindly: absent timestamps are
        stored as 0 and would otherwise drag the floor to the epoch. Logs are
        overwhelmingly already in order, so the common case is two lookups.
        """
        ts = self.timestamps
        n = len(ts)
        lo = 0
        while lo < n and ts[lo] == 0:
            lo += 1
        if lo == n:
            return 0, 0
        hi = n - 1
        while hi > lo and ts[hi] == 0:
            hi -= 1
        first, last = ts[lo], ts[hi]
        if first <= last:
            # Still verify: an out-of-order paste would give a bogus span.
            nonzero_min = first
            nonzero_max = last
            for i in range(lo, hi + 1):
                v = ts[i]
                if v == 0:
                    continue
                if v < nonzero_min:
                    nonzero_min = v
                elif v > nonzero_max:
                    nonzero_max = v
            return nonzero_min, nonzero_max
        return last, first

    def memory_bytes(self) -> int:
        """Approximate retained size, for the benchmark's per-record figure."""
        import sys

        total = (
            self.timestamps.buffer_info()[1] * self.timestamps.itemsize
            + self.severities.buffer_info()[1] * self.severities.itemsize
            + self.source_ids.buffer_info()[1] * self.source_ids.itemsize
            + sys.getsizeof(self.bodies)
            + sys.getsizeof(self.trace_ids)
        )
        total += sum(sys.getsizeof(b) for b in self.bodies)
        if self.attributes is not None:
            total += sys.getsizeof(self.attributes)
            total += sum(sys.getsizeof(a) for a in self.attributes)
        return total
