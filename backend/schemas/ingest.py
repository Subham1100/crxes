"""Wire shapes for the ingest phase.

The preview endpoint exists so the upload UI can show what ingest made of a
file set — detected formats, redactions, correlation coverage — before the
user commits to a run. Nothing is persisted and nothing is sent to a model, so
it is cheap to call on every change to the file list.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from ingest import FORMATS, ROLES, FileReport, IngestResult, RedactionPolicy, SourceFile
from ingest.limits import MAX_FILES, MAX_UPLOAD_BYTES

Role = Literal[
    "frontend", "proxy", "gateway", "backend", "worker", "queue", "cache", "db", "external", "unknown"
]
LogFormat = Literal["plain", "json", "logfmt", "clf", "syslog", "syslog5424", "postgres", "cri"]

#: Guards the annotations against the source of truth drifting out from under
#: them — `Literal` cannot be built from a runtime tuple without losing the
#: static type, so the two are cross-checked at import instead.
assert set(ROLES) == set(Role.__args__), "ROLES and the Role literal disagree"
assert set(FORMATS) == set(LogFormat.__args__), "FORMATS and the LogFormat literal disagree"


class RedactionOptions(BaseModel):
    enabled: bool = True
    #: Off by default: an IP is a correlation key more often than it is PII.
    redact_ips: bool = False
    redact_cards: bool = True
    disabled_rules: list[str] = Field(default_factory=list)

    def to_policy(self) -> RedactionPolicy:
        return RedactionPolicy(
            enabled=self.enabled,
            redact_ips=self.redact_ips,
            redact_cards=self.redact_cards,
            disabled_rules=frozenset(self.disabled_rules),
        )


class FileIn(BaseModel):
    """One file in an ingest request."""

    name: str = Field(min_length=1, max_length=255)
    content: str = Field(max_length=MAX_UPLOAD_BYTES)
    #: Naming the service turns off per-line service inference for this file.
    #: Leave it unset for a paste of several services' interleaved output.
    service: str | None = Field(default=None, max_length=128)
    role: Role = "unknown"
    #: Overrides detection. Leave unset to sniff it.
    format: LogFormat | None = None
    #: IANA zone, used to resolve timestamps that carry no offset.
    timezone: str | None = Field(default=None, max_length=64)
    #: Manual clock-skew correction, ±24h.
    offset_seconds: int = Field(default=0, ge=-86_400, le=86_400)

    def to_source(self) -> SourceFile:
        return SourceFile(
            name=self.name,
            content=self.content,
            service=self.service,
            role=self.role,
            format=self.format,
            timezone=self.timezone,
            offset_seconds=self.offset_seconds,
        )


class IngestRequest(BaseModel):
    files: Annotated[list[FileIn], Field(min_length=1, max_length=MAX_FILES)]
    redaction: RedactionOptions = Field(default_factory=RedactionOptions)
    #: Entries returned in `sample`. The full set can be half a million rows,
    #: which is not something to push through a preview response.
    sample_limit: int = Field(default=100, ge=0, le=1000)


class FileReportOut(BaseModel):
    file_id: str
    name: str
    service: str
    role: str
    format: str
    format_confidence: float
    flavor: str | None
    format_overridden: bool
    entry_count: int
    line_count: int
    bytes: int
    first_timestamp: str | None
    last_timestamp: str | None
    unresolved_timestamps: int
    applied_offset_seconds: int
    skew_warning: str | None
    dropped_lines: int
    with_trace_id: int
    with_correlation_key: int

    @classmethod
    def of(cls, report: FileReport) -> "FileReportOut":
        return cls(
            file_id=report.file_id,
            name=report.name,
            service=report.service,
            role=report.role,
            format=report.format,
            format_confidence=report.format_confidence,
            flavor=report.flavor,
            format_overridden=report.format_overridden,
            entry_count=report.entry_count,
            line_count=report.line_count,
            bytes=report.bytes,
            first_timestamp=report.first_timestamp.isoformat() if report.first_timestamp else None,
            last_timestamp=report.last_timestamp.isoformat() if report.last_timestamp else None,
            unresolved_timestamps=report.unresolved_timestamps,
            applied_offset_seconds=report.applied_offset_seconds,
            skew_warning=report.skew_warning,
            dropped_lines=report.dropped_lines,
            with_trace_id=report.with_trace_id,
            with_correlation_key=report.with_correlation_key,
        )


class RedactionOut(BaseModel):
    """What redaction removed. Rendered as the "before you send this" summary."""

    total: int
    entries_affected: int
    #: Rule name → count. The UI lists these so the user sees what went.
    counts: dict[str, int]


class IngestPreviewOut(BaseModel):
    files: list[FileReportOut]
    entry_count: int
    dropped_lines: int
    #: Share of entries carrying a real trace ID, 0.0–1.0. At 1.0 the next
    #: phase renders; at 0.0 every cross-service link has to be inferred.
    trace_coverage: float
    #: Share carrying at least one explicit join key — the fallback when
    #: trace coverage is 0.
    correlation_coverage: float
    first_timestamp: str | None
    last_timestamp: str | None
    skew_suspected: bool
    redaction: RedactionOut
    sample: list[dict[str, Any]]

    @classmethod
    def of(cls, result: IngestResult, sample_limit: int = 100) -> "IngestPreviewOut":
        first, last = result.time_range()
        with_keys = sum(1 for e in result.entries if e.correlation_keys)
        return cls(
            files=[FileReportOut.of(f) for f in result.files],
            entry_count=result.entry_count,
            dropped_lines=result.dropped_lines,
            trace_coverage=round(result.trace_coverage, 4),
            correlation_coverage=round(with_keys / result.entry_count, 4)
            if result.entry_count
            else 0.0,
            first_timestamp=first.isoformat() if first else None,
            last_timestamp=last.isoformat() if last else None,
            skew_suspected=result.skew_suspected,
            redaction=RedactionOut(
                total=result.redaction.total,
                entries_affected=result.redaction.entries_affected,
                counts=dict(result.redaction.counts),
            ),
            sample=[e.to_dict() for e in result.entries[:sample_limit]],
        )


__all__ = [
    "FileIn",
    "FileReportOut",
    "IngestPreviewOut",
    "IngestRequest",
    "LogFormat",
    "RedactionOptions",
    "RedactionOut",
    "Role",
]
