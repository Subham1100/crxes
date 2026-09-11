"""JSON logs, in every dialect that shows up in practice.

One parser rather than seven, because the dialects differ only in where the
same handful of values live. The flavor picked during detection selects a
`FieldMap`; everything after that is shared. The exception is the OTLP
envelope, which nests many records inside one document and gets its own
unwrapping pass.

Whatever a dialect does not claim is kept: unmapped keys land in `attributes`
rather than being dropped, because the field this parser has never heard of is
routinely the one that identifies the request.
"""

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterator

from ingest.entry import Entry
from ingest.parsers.common import (
    level_from_name,
    level_from_number,
    parse_iso,
)
from ingest.parsers.plain import build_entry

if TYPE_CHECKING:
    from ingest.parsers import ParseContext

# --- Field maps --------------------------------------------------------------

#: Candidate key names per logical field, most specific first. Shared by every
#: dialect; a flavor's own map is consulted before this one.
_GENERIC: dict[str, tuple[str, ...]] = {
    "timestamp": ("timestamp", "@timestamp", "time", "ts", "eventTime", "asctime", "Timestamp"),
    "level": ("level", "severity", "levelname", "log.level", "lvl", "status", "SeverityText",
              "severityText"),
    "message": ("message", "msg", "body", "Body", "event", "short_message", "text", "log"),
    "trace_id": ("trace_id", "traceId", "TraceId", "trace.id", "dd.trace_id"),
    "span_id": ("span_id", "spanId", "SpanId", "span.id", "dd.span_id"),
    "parent_span_id": ("parent_span_id", "parentSpanId", "ParentSpanId", "parent_id"),
    "service": ("service", "service.name", "serviceName", "dd.service", "component", "logger",
                "logger_name", "name"),
}

#: Keys that are join candidates wherever they appear. Full entity mining is
#: the enrich phase's job; these are the ones a format hands over outright.
_CORRELATION_KEYS: tuple[str, ...] = (
    "request_id",
    "requestId",
    "req_id",
    "reqId",
    "correlation_id",
    "correlationId",
    "session_id",
    "sessionId",
    "user_id",
    "userId",
    "transaction_id",
    "transactionId",
    "insertId",
    "pid",
    "thread",
    "thread_name",
)

#: Per-flavor overrides layered over `_GENERIC`.
_FLAVORS: dict[str, dict[str, tuple[str, ...]]] = {
    "gcp": {
        "timestamp": ("timestamp", "receiveTimestamp"),
        "level": ("severity",),
        "message": ("textPayload", "message", "jsonPayload.message", "jsonPayload.msg",
                    "protoPayload.status.message"),
        "trace_id": ("logging.googleapis.com/trace", "trace"),
        "span_id": ("logging.googleapis.com/spanId", "spanId"),
        "service": ("resource.labels.service_name", "resource.labels.container_name",
                    "resource.labels.function_name", "resource.labels.module_id",
                    "logging.googleapis.com/sourceLocation.file"),
    },
    "datadog": {
        "level": ("status", "level", "syslog.severity"),
        "trace_id": ("dd.trace_id", "trace_id"),
        "span_id": ("dd.span_id", "span_id"),
        "service": ("service", "dd.service", "ddsource"),
    },
    "ecs": {
        "timestamp": ("@timestamp",),
        "level": ("log.level", "level"),
        "trace_id": ("trace.id",),
        "span_id": ("span.id",),
        "parent_span_id": ("parent.id",),
        "service": ("service.name", "service"),
    },
    "otlp": {
        "timestamp": ("Timestamp", "timestamp", "timeUnixNano", "observedTimeUnixNano"),
        "level": ("SeverityText", "severityText"),
        "message": ("Body", "body"),
        "trace_id": ("TraceId", "traceId", "trace_id"),
        "span_id": ("SpanId", "spanId", "span_id"),
        "service": ("Resource.service.name", "resource.service.name"),
    },
    "bunyan": {"message": ("msg",), "service": ("name",)},
    "pino": {"message": ("msg",), "service": ("name",)},
}

#: Keys consumed into first-class `Entry` fields, so they are not duplicated
#: into `attributes`. Populated per-entry from whichever candidate matched.
_ALWAYS_SKIP: frozenset[str] = frozenset({"v"})

#: GCP prefixes its trace with the project path.
_GCP_TRACE_PREFIX = "projects/"


def _dig(obj: dict, path: str) -> Any:
    """Look up `path` in `obj`, literal key first, then as a dotted path.

    Both spellings occur, sometimes in the same file: ECS permits `log.level`
    as a flat key or as `{"log": {"level": ...}}`, and a collector may flatten
    one and not the other.
    """
    if path in obj:
        return obj[path]
    if "." not in path:
        return None
    node: Any = obj
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _first(obj: dict, candidates: tuple[str, ...]) -> tuple[Any, str | None]:
    """First non-empty value among `candidates`; also returns the key that hit."""
    for key in candidates:
        value = _dig(obj, key)
        if value is not None and value != "":
            return value, key
    return None, None


def _fieldmap(flavor: str | None) -> dict[str, tuple[str, ...]]:
    overrides = _FLAVORS.get(flavor or "", {})
    return {
        field: overrides.get(field, ()) + _GENERIC[field]
        for field in _GENERIC
    }


# --- Value coercion ----------------------------------------------------------


def _epoch_to_datetime(value: float) -> datetime | None:
    """Convert an epoch number to UTC, inferring its unit from magnitude.

    Seconds, milliseconds, microseconds and nanoseconds all appear — OTel uses
    nanoseconds, Datadog milliseconds, most everyone else seconds. The bands
    are wide enough apart that magnitude identifies the unit unambiguously for
    any timestamp this side of 1973.
    """
    magnitude = abs(value)
    if magnitude >= 1e17:  # nanoseconds
        value /= 1e9
    elif magnitude >= 1e14:  # microseconds
        value /= 1e6
    elif magnitude >= 1e11:  # milliseconds
        value /= 1e3
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    except (OSError, OverflowError, ValueError):
        return None


def _to_datetime(value: Any) -> datetime | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _epoch_to_datetime(float(value))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # OTel writes `timeUnixNano` as a decimal string, not a number.
        if text.isdigit():
            return _epoch_to_datetime(float(text))
        return parse_iso(text)
    return None


def _unwrap_anyvalue(value: Any) -> Any:
    """Flatten OTLP's `AnyValue` union into a plain Python value.

    OTel's JSON encoding wraps every value in a single-key object naming its
    type (`{"stringValue": "x"}`), which is faithful to the protobuf and
    useless to read, so it is unwrapped on the way in.
    """
    if not isinstance(value, dict) or len(value) != 1:
        return value
    key, inner = next(iter(value.items()))
    if key in ("stringValue", "boolValue"):
        return inner
    if key == "intValue":
        try:
            return int(inner)
        except (TypeError, ValueError):
            return inner
    if key == "doubleValue":
        return inner
    if key == "arrayValue":
        return [_unwrap_anyvalue(v) for v in (inner or {}).get("values", [])]
    if key == "kvlistValue":
        return _otlp_attributes((inner or {}).get("values", []))
    if key == "bytesValue":
        return inner
    return value


def _otlp_attributes(items: Any) -> dict[str, Any]:
    """Turn OTel's `[{key, value}]` attribute list into a dict."""
    if isinstance(items, dict):
        return {k: _unwrap_anyvalue(v) for k, v in items.items()}
    if not isinstance(items, list):
        return {}
    return {
        item["key"]: _unwrap_anyvalue(item.get("value"))
        for item in items
        if isinstance(item, dict) and "key" in item
    }


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    if isinstance(value, dict):
        unwrapped = _unwrap_anyvalue(value)
        if isinstance(unwrapped, str):
            return unwrapped
        return json.dumps(unwrapped, separators=(",", ":"), default=str)
    if isinstance(value, (list, tuple)):
        return json.dumps(value, separators=(",", ":"), default=str)
    return str(value)


def _hexish(value: Any) -> str | None:
    """Normalize a trace/span ID to a string, or reject it.

    Datadog emits 64-bit decimal integers where OTel emits hex, and both are
    valid identities within their own system. They are kept verbatim — the goal
    is a stable join key, not a canonical encoding — but empty and all-zero IDs
    are rejected, because an unsampled span writes zeros and treating those as
    a real trace would merge every unsampled request into one.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in ("0", "00000000000000000000000000000000", "0000000000000000"):
        return None
    if text.startswith(_GCP_TRACE_PREFIX):
        text = text.rsplit("/", 1)[-1]
    return text or None


# --- OTLP envelope -----------------------------------------------------------


def _otlp_records(doc: dict) -> Iterator[tuple[dict, dict, dict]]:
    """Yield `(logRecord, resourceAttributes, scopeAttributes)` from an envelope.

    A collector's file exporter writes one of these per flush, holding every
    record in the batch, so a 200k-line dump can be four physical lines.
    """
    for resource_log in doc.get("resourceLogs") or doc.get("resource_logs") or []:
        if not isinstance(resource_log, dict):
            continue
        resource = _otlp_attributes(
            (resource_log.get("resource") or {}).get("attributes", [])
        )
        scopes = resource_log.get("scopeLogs") or resource_log.get("scope_logs") or []
        for scope_log in scopes:
            if not isinstance(scope_log, dict):
                continue
            scope = scope_log.get("scope") or {}
            scope_attrs = _otlp_attributes(scope.get("attributes", []))
            if scope.get("name"):
                scope_attrs["scope.name"] = scope["name"]
            records = scope_log.get("logRecords") or scope_log.get("log_records") or []
            for record in records:
                if isinstance(record, dict):
                    yield record, resource, scope_attrs


# --- Entry construction ------------------------------------------------------


def _build(
    obj: dict,
    line_no: int,
    ctx: "ParseContext",
    fieldmap: dict[str, tuple[str, ...]],
    raw: str,
    extra_attributes: dict[str, Any] | None = None,
    service_hint: str | None = None,
) -> Entry:
    consumed: set[str] = set()

    def take(field: str) -> Any:
        value, key = _first(obj, fieldmap[field])
        if key is not None:
            consumed.add(key)
        return value

    timestamp = _to_datetime(take("timestamp"))

    level_value = take("level")
    if isinstance(level_value, (int, float)) and not isinstance(level_value, bool):
        scale = "otel" if ctx.flavor == "otlp" else "auto"
        level = level_from_number(level_value, scale=scale)
    else:
        level = level_from_name(_stringify(level_value) or None)
    # OTLP carries a numeric severity alongside the text one; prefer the text,
    # fall back to the number.
    if level_value is None:
        number, key = _first(obj, ("SeverityNumber", "severityNumber", "severity_number"))
        if number is not None:
            consumed.add(key or "")
            level = level_from_number(number, scale="otel")

    message = _stringify(take("message"))

    # The user's label always wins; the log's own name is kept as an attribute
    # below so nothing is lost. `service_hint` carries the resource-level
    # `service.name` from an OTLP envelope, which lives outside the record.
    service = ctx.service
    service_value = take("service")
    if not ctx.service_declared:
        service = _stringify(service_value) or service_hint or service

    attributes: dict[str, Any] = {}
    if extra_attributes:
        attributes.update(extra_attributes)

    # Anything the field map did not claim is kept verbatim. Nested OTel
    # attribute lists are flattened on the way in so later stages see values,
    # not encodings.
    for key, value in obj.items():
        if key in consumed or key in _ALWAYS_SKIP:
            continue
        if key in ("attributes", "Attributes") and isinstance(value, (list, dict)):
            attributes.update(_otlp_attributes(value))
            continue
        if key in ("Resource", "resource") and isinstance(value, (list, dict)):
            attributes.update(
                {f"resource.{k}": v for k, v in _otlp_attributes(value).items()}
            )
            continue
        attributes[key] = _unwrap_anyvalue(value) if isinstance(value, dict) else value

    if service_value and ctx.service_declared:
        attributes.setdefault("logger", _stringify(service_value))

    correlation: dict[str, str] = {}
    for key in _CORRELATION_KEYS:
        value = _dig(obj, key)
        if value is None:
            value = attributes.get(key)
        if value not in (None, ""):
            correlation[key] = str(value)

    if not message:
        # A record with no recognisable message field is still an event. Keep
        # the whole document as the message rather than emitting a blank line.
        message = json.dumps(obj, separators=(",", ":"), default=str)[:2000]

    return Entry(
        file_id=ctx.file_id,
        line_no=line_no,
        service=service,
        role=ctx.role,
        timestamp=timestamp,
        time_source="offset" if timestamp else "none",
        level=level,
        message=message,
        trace_id=_hexish(take("trace_id")),
        span_id=_hexish(take("span_id")),
        parent_span_id=_hexish(take("parent_span_id")),
        attributes=attributes,
        correlation_keys=correlation,
        raw=raw,
    )


def parse(text: str, ctx: "ParseContext") -> Iterator[Entry]:
    """Parse newline-delimited JSON, tolerating non-JSON lines mixed in.

    Interleaved plain text is common — a crash dump, a framework banner, a
    line written to the same stream by something that does not log as JSON —
    and it is parsed as plain text rather than discarded.
    """
    fieldmap = _fieldmap(ctx.flavor)
    line_no = 0

    for line in text.splitlines():
        line_no += 1
        stripped = line.strip()
        if not stripped:
            continue

        if not stripped.startswith("{"):
            yield build_entry(line, [], line_no, ctx)
            continue

        try:
            doc = json.loads(stripped)
        except ValueError:
            yield build_entry(line, [], line_no, ctx)
            continue

        if not isinstance(doc, dict):
            yield build_entry(line, [], line_no, ctx)
            continue

        # Docker's json-file driver wraps a real log line in an envelope; the
        # payload is what actually matters, so it is unwrapped and re-parsed.
        if ctx.flavor == "docker" and "log" in doc and "stream" in doc:
            inner = str(doc.get("log", "")).rstrip("\n")
            entry = build_entry(inner, [], line_no, ctx, fallback_ts=_to_datetime(doc.get("time")))
            entry.attributes.setdefault("stream", doc.get("stream"))
            entry.raw = line
            yield entry
            continue

        if "resourceLogs" in doc or "resource_logs" in doc:
            for record, resource, scope in _otlp_records(doc):
                merged = {f"resource.{k}": v for k, v in resource.items()}
                merged.update({f"scope.{k}": v for k, v in scope.items()})
                yield _build(
                    record,
                    line_no,
                    ctx,
                    _fieldmap("otlp"),
                    raw=json.dumps(record, separators=(",", ":"), default=str)[:4000],
                    extra_attributes=merged,
                    service_hint=_stringify(resource.get("service.name")) or None,
                )
            continue

        yield _build(doc, line_no, ctx, fieldmap, raw=line)


__all__ = ["parse"]
