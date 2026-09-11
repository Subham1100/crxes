"""The normalized log entry, and the file description that produces one.

Everything downstream — correlation, digest, root-cause localization, the UI —
sees `Entry` and nothing else. Adding a log format means writing a parser that
emits these; it never means teaching a later stage about a new shape.

`Entry` is a slotted dataclass rather than a Pydantic model because ingest is
the only stage that touches every line: a 500k-line dump allocates 500k of
these, and slots cut both the per-object memory and the attribute lookups in
the parse loop. Validation happens at the API boundary, on the way in and out.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

#: Where a service sits in a request's path. Ordered shallow → deep: a request
#: enters at the frontend and bottoms out at a datastore. Root-cause
#: localization leans on this — when several services report an error at once,
#: the deepest one is the origin and the shallow ones are reporting its blast
#: radius. That inference is only as good as these labels, which is why the
#: upload UI asks for them per file.
ROLES: tuple[str, ...] = (
    "frontend",
    "proxy",
    "gateway",
    "backend",
    "worker",
    "queue",
    "cache",
    "db",
    "external",
    "unknown",
)

#: Depth per role. "unknown" and "external" sit outside the ordering and get
#: `None`, so a missing label produces no inference rather than a wrong one.
ROLE_DEPTH: dict[str, int | None] = {
    "frontend": 0,
    "proxy": 1,
    "gateway": 2,
    "backend": 3,
    "worker": 4,
    "queue": 5,
    "cache": 6,
    "db": 7,
    "external": None,
    "unknown": None,
}

#: Canonical severities, ordered least → most severe. Every parser maps its
#: format's vocabulary onto these, so a Postgres `PANIC`, a syslog priority of
#: 0 and an OTel `SeverityNumber` of 21 all arrive as "fatal".
LEVELS: tuple[str, ...] = ("trace", "debug", "info", "warn", "error", "critical", "fatal")

LEVEL_RANK: dict[str, int] = {name: i for i, name in enumerate(LEVELS)}

#: Levels that mark an entry as a failure signal. Root-cause localization walks
#: backward through these; the digest counts them per bucket.
FAILURE_LEVELS: frozenset[str] = frozenset({"error", "critical", "fatal"})


@dataclass(slots=True)
class SourceFile:
    """One uploaded file, or one paste, with whatever the user told us about it."""

    name: str
    content: str
    #: The emitting service. Defaults to the filename stem — `api.log` → `api`,
    #: which is right often enough to be a useful prefill and always editable.
    service: str | None = None
    role: str = "unknown"
    #: A user override for detection. `None` means sniff it.
    format: str | None = None
    #: IANA zone used to resolve *naive* timestamps. Ignored for lines that
    #: carry their own offset.
    timezone: str | None = None
    #: Manual clock-skew correction, added to every timestamp in this file.
    offset_seconds: int = 0

    def resolved_service(self) -> str:
        if self.service:
            return self.service
        stem = self.name.rsplit("/", 1)[-1]
        for suffix in (".log", ".txt", ".json", ".ndjson", ".jsonl", ".gz"):
            if stem.endswith(suffix):
                stem = stem[: -len(suffix)]
        return stem or "unknown"


@dataclass(slots=True)
class Entry:
    """One log event. Not one log *line* — a stack trace folds into its entry."""

    file_id: str
    #: 1-based line number of the entry's first line, within its own file. The
    #: UI drills back to the raw text with this.
    line_no: int
    service: str
    role: str
    #: UTC, or `None` when the line carried no timestamp and none could be
    #: carried forward. Never a naive datetime past `normalize`.
    timestamp: datetime | None
    #: How `timestamp` was arrived at: "offset" (the line carried a UTC offset),
    #: "zone" (naive, resolved against the file's declared timezone), "assumed"
    #: (naive, no zone declared, treated as UTC), "carried" (no timestamp on the
    #: line; inherited from the preceding entry), or "none".
    time_source: str
    level: str
    message: str

    #: Real trace context, and only ever real — populated from the log itself.
    #: Correlation writes its inferred grouping to `flow_id`, never here, so
    #: that "the app told us" stays distinguishable from "we worked it out".
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None

    #: Structured fields the format supplied natively (JSON keys, logfmt pairs,
    #: CLF fields). Mining numerics out of *unstructured* message text is the
    #: enrich phase's job, not this one.
    attributes: dict[str, Any] = field(default_factory=dict)
    #: Explicit join keys the format handed us — request IDs, PIDs, session
    #: IDs. Entity-key mining is likewise the enrich phase.
    correlation_keys: dict[str, str] = field(default_factory=dict)

    raw: str = ""
    #: Set by the enrich phase (Drain3). Present here so the shape stays stable.
    template_id: str | None = None
    #: Set by correlation. Present for the same reason.
    flow_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """The JSONB representation stored on `LogPull.normalized_logs`."""
        return {
            "file_id": self.file_id,
            "line_no": self.line_no,
            "service": self.service,
            "role": self.role,
            "timestamp": self.timestamp.isoformat() if self.timestamp else "",
            "time_source": self.time_source,
            "level": self.level,
            "message": self.message,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "attributes": self.attributes,
            "correlation_keys": self.correlation_keys,
            "raw": self.raw,
            "template_id": self.template_id,
            "flow_id": self.flow_id,
        }
