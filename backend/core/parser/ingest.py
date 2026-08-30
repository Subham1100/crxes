"""Stage 1 — anything that looks like logs becomes a `RecordBatch`.

Three input shapes are handled, sniffed automatically:

* **otlp**  — an OTLP/JSON `ExportLogsServiceRequest`, the wire format an
  OpenTelemetry collector emits. Everything is already in the right shape.
* **jsonl** — one JSON object per line, which is what Vector, Fluent Bit, Loki,
  and most structured application loggers produce. Field *names* vary wildly,
  so they are resolved once against the first record and reused.
* **text**  — unstructured lines. Timestamp, level and logger name are guessed.

The text path is the one with real work in it, and it is written accordingly:
bound methods hoisted into locals, no regex where a character comparison will
do, and at most two string allocations per line. The prefix-anchored extraction
is also *more* correct than searching the whole line — an "error" in the middle
of a message no longer sets the record's severity.

No line cap lives here. `core.logs.MAX_LINES` exists to bound what gets sent to
a model; this stage is expected to eat whole files, and the caps belong at the
API edge where the paste arrives.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Iterable

from core.parser import timestamps
from core.parser.model import (
    SEV_DEBUG,
    SEV_ERROR,
    SEV_FATAL,
    SEV_INFO,
    SEV_TRACE,
    SEV_UNSPECIFIED,
    SEV_WARN,
    SEVERITY_BY_NAME,
    RecordBatch,
)
from core.parser.timing import Timings

#: OTel's own fallback when a resource declares no `service.name`.
UNKNOWN_SOURCE = "unknown_service"

#: A folded stack trace stops growing here. Without a bound, one pathological
#: continuation block would make the fold quadratic and the body unbounded.
MAX_BODY_CHARS = 8_192
MAX_CONTINUATION_LINES = 256


def _severity_lookup() -> dict[str, int]:
    """Level names in the three casings that actually occur, so the hot loop
    can do a dict hit instead of allocating a `.upper()` per line."""
    out: dict[str, int] = {}
    for name, number in SEVERITY_BY_NAME.items():
        out[name] = number
        out[name.lower()] = number
        out[name.capitalize()] = number
    return out


_SEVERITY = _severity_lookup()

#: A level token sitting at the front of the remaining text, optionally
#: bracketed, followed by separator punctuation.
#:
#: Deliberately a generic word rather than an alternation of the ~60 known level
#: spellings. Python's `re` walks an alternation branch by branch, which
#: measured *slower* than matching any word and settling it with one dict
#: lookup (264 ns against 208 ns) — and the dict lookup is what decides whether
#: the token is consumed, so a message beginning with an ordinary word is never
#: mistaken for a level.
_LEVEL_AT = re.compile(r"[\[(<]?([A-Za-z]{3,11})[\])>]?[\s:|/\-]+")

#: `[api.handler]` / `(worker-3)` — a bracketed logger or service name.
_SOURCE_AT = re.compile(r"[\[(]([\w.\-/:]{2,64})[\])][\s:|\-]*")

#: Timestamp, level and logger in one match.
#:
#: A log file is homogeneous — one producer, one layout — so the line shape
#: that matches the first record matches essentially all of them. Three
#: separate scans meant three round trips through the regex engine; folding
#: them into one pattern means the common line costs a single C call, and the
#: general path below runs only for the stragglers.
#:
#: Groups 1-4 are the timestamp (see `timestamps.ISO_PATTERN`), 5 the level,
#: 6 the bracketed logger; 5 and 6 are optional. Because group 5 matches any
#: word, it will happily consume the first word of a message on a log with no
#: level field — so the caller checks the dict and rewinds to `start(5)` when
#: the token turns out not to be a level.
_FAST_PREFIX = re.compile(
    timestamps.ISO_PATTERN
    + timestamps.TRAILING
    + r"(?:[\[(<]?([A-Za-z]{3,11})[\])>]?[\s:|/\-]+)?"
    + r"(?:[\[(]([\w.\-/:]{2,64})[\])][\s:|\-]*)?"
)

#: `checkout[1234]:` — syslog's tag/pid form. The bracketed pid is what makes
#: this safe to accept as a bare word where `_BARE_SOURCE_AT` would not.
_TAG_PID_AT = re.compile(r"([A-Za-z][\w.\-/]{1,63})\[\d+\]:?[\s:|\-]*")

#: `api.handler:` unbracketed. The interior dot/dash/slash is what separates a
#: logger name from an ordinary capitalised word followed by a colon — without
#: it, `Error: connection refused` would report a service called "Error".
_BARE_SOURCE_AT = re.compile(r"([A-Za-z][\w\-]*(?:[.\-/][\w\-]+)+)\s*[:|]\s+")

#: `myhost package_script_service[27201]:` — full RFC3164, which puts a
#: hostname between the timestamp and the tag. Tried last because it is the
#: loosest of the four; the required trailing colon is what keeps it from
#: matching two ordinary words of a message.
_HOST_TAG_AT = re.compile(r"[\w][\w.\-]{0,63}\s+([A-Za-z][\w.\-/]{1,63})(?:\[\d+\])?:\s*")

#: `nova.osapi_compute.wsgi.server ` — a dotted logger with no delimiter around
#: it, as oslo.log and `logging.Formatter('%(name)s')` emit. Two dots minimum:
#: one would match an ordinary sentence ending in an abbreviation.
_DOTTED_SOURCE_AT = re.compile(r"([A-Za-z][\w\-]*(?:\.[\w\-]+){2,})[\s:|\-]+")

#: A process or thread id between the timestamp and the level, which is where
#: oslo.log and Python's logging default both put one. Only consumed if a level
#: turns out to follow it — otherwise a message opening with a number would
#: lose its first word.
_PID_AT = re.compile(r"\d{1,9}\s+")

#: How far into a line to look for a timestamp that is not at column 0. Covers
#: a log filename, a k8s pod prefix, or a `docker compose` service column.
_PREFIX_SCAN_LIMIT = 200

#: Punctuation to peel off a candidate level word in the fallback below.
_LEVEL_TRIM = "[]()<>:,|="


def level_in_head(body: str) -> int:
    """Last resort: look for a level among the first few words of the body.

    Splitting and doing four dict lookups beats a regex here by a wide margin —
    an alternation of every level spelling measured at 2.7 microseconds per
    search, against roughly 0.4 for this. The token is left *in* the body:
    removing it would cost an allocation, and a constant token is harmless once
    templating collapses it into the pattern.
    """
    lookup = _SEVERITY.get
    for word in body[:60].split(None, 4)[:4]:
        found = lookup(word)
        if found is None:
            found = lookup(word.strip(_LEVEL_TRIM))
        if found is not None:
            return found
    return SEV_UNSPECIFIED

#: Continuation lines that do not begin with whitespace.
_CONT_PREFIXES = ("Caused by:", "at ", "...", "Suppressed:", "Traceback (", "File \"")

#: How many records to watch before deciding whether this stream is timestamped.
#: Once it is, an untimestamped line is a continuation of the record above —
#: which is the only general way to catch an exception *header* like
#: `java.sql.SQLException: Connection is not available`, since it starts at
#: column zero and matches none of the prefixes above. Filebeat and Fluent Bit
#: settle multiline detection the same way.
_PROBE_RECORDS = 20
_PROBE_MIN_TIMESTAMPED = 16

#: Python's `logging` levelno scale, which collides numerically with OTel's.
_PYTHON_LEVELNO = {10: SEV_DEBUG, 20: SEV_INFO, 30: SEV_WARN, 40: SEV_ERROR, 50: SEV_FATAL}

#: Syslog PRI severity — lower is worse, the opposite of every other scale.
_SYSLOG_PRI = {
    0: SEV_FATAL + 3, 1: SEV_FATAL + 1, 2: SEV_FATAL, 3: SEV_ERROR,
    4: SEV_WARN, 5: SEV_INFO + 1, 6: SEV_INFO, 7: SEV_DEBUG,
}


# --------------------------------------------------------------------------
# Format detection
# --------------------------------------------------------------------------

def sniff(sample: str) -> str:
    """Classify an input as `otlp`, `jsonl` or `text` from its first lines."""
    head = sample[:65_536].lstrip()
    if not head:
        return "text"
    if head[0] == "{":
        if '"resourceLogs"' in head[:4096] or '"resource_logs"' in head[:4096]:
            return "otlp"
        first = head.split("\n", 1)[0]
        try:
            json.loads(first)
        except ValueError:
            # A pretty-printed OTLP document breaks across lines, so a failed
            # single-line decode still leaves the whole-document case open.
            return "otlp" if '"logRecords"' in head else "text"
        return "jsonl"
    return "text"


# --------------------------------------------------------------------------
# Text
# --------------------------------------------------------------------------

def _ingest_text(batch: RecordBatch, lines: Iterable[str], default_year: int) -> None:
    stats = batch.stats
    bodies = batch.bodies
    sources = batch.sources
    source_index = batch._source_index

    ts_append = batch.timestamps.append
    sev_append = batch.severities.append
    src_append = batch.source_ids.append
    body_append = bodies.append
    trace_append = batch.trace_ids.append
    attributes = batch.attributes
    attr_append = attributes.append if attributes is not None else None

    scan = timestamps.scan
    day_seconds = timestamps.day_seconds
    time_of_day = timestamps.time_of_day
    zone_seconds = timestamps.zone_seconds
    # Read straight out of the caches rather than through their accessors: on a
    # hit — which is nearly every line — the call overhead exceeds the lookup.
    day_cache = timestamps.DAY_CACHE.get
    tod_cache = timestamps.TOD_CACHE.get
    pow10 = timestamps._POW10
    ns_per_sec = timestamps.NS_PER_SEC

    severity_of = _SEVERITY.get
    fast_prefix = _FAST_PREFIX.match
    fast_search = _FAST_PREFIX.search
    level_at = _LEVEL_AT.match
    source_at = _SOURCE_AT.match
    tag_pid_at = _TAG_PID_AT.match
    bare_source_at = _BARE_SOURCE_AT.match
    host_tag_at = _HOST_TAG_AT.match
    dotted_source_at = _DOTTED_SOURCE_AT.match
    pid_at = _PID_AT.match
    level_head = level_in_head

    total = blank = cont = with_ts = with_sev = nbytes = emitted = 0
    unknown_id = batch.source_id(UNKNOWN_SOURCE)

    # Set once `_PROBE_RECORDS` records have gone by; until then an
    # untimestamped line is taken at face value as its own record.
    timestamped = False
    decided = False

    # The column the timestamp starts at. Not always 0: OpenStack prefixes each
    # line with its log filename, `kubectl logs --prefix` with pod/container,
    # `docker compose` with a service column. The offset is learned from the
    # first line that needs it and reused, so the common case stays a single
    # anchored match; a line whose prefix is a different width simply misses and
    # re-learns.
    ts_offset = 0
    # Searching for a displaced timestamp is only worth it on a file that has
    # one. A syslog or CLF file matches none of `_FAST_PREFIX`, so without this
    # every line would pay a fruitless 200-character search — measured at a
    # third of throughput on `/var/log/install.log`. Switched off after the
    # probe window if it never paid off.
    prefix_search = True
    prefix_hits = 0

    # Continuation lines accumulate here and are folded onto the previous body
    # when the next real record starts. Appending to `bodies[-1]` directly would
    # be quadratic: the list holds a second reference, so CPython's in-place
    # concatenation optimisation does not apply.
    pending: list[str] = []
    pending_chars = 0

    for raw in lines:
        total += 1
        nbytes += len(raw)
        line = raw.rstrip()
        if not line:
            blank += 1
            continue

        first = line[0]
        is_continuation = bodies and (
            first == " " or first == "\t" or line.startswith(_CONT_PREFIXES)
        )

        nanos = pos = 0
        level = SEV_UNSPECIFIED
        source_name = None
        fast = None

        if not is_continuation:
            fast = fast_prefix(line, ts_offset)
            if fast is None and prefix_search:
                fast = fast_search(line, 0, _PREFIX_SCAN_LIMIT)
                if fast is not None:
                    ts_offset = fast.start()
                    prefix_hits += 1
            if fast is not None:
                # One `group()` call for all six, not six calls.
                date_s, tod_s, frac_s, zone_s, token, src_s = fast.group(1, 2, 3, 4, 5, 6)
                day = day_cache(date_s)
                if day is None:
                    try:
                        day = day_seconds(date_s)
                    except ValueError:
                        day = None  # 2025-02-30 and friends — fall through.
                if day is None:
                    fast = None
                else:
                    # Not `or` — midnight caches as 0, which is falsy.
                    tod = tod_cache(tod_s)
                    secs = day + (time_of_day(tod_s) if tod is None else tod)
                    if zone_s is not None and zone_s != "Z" and zone_s != "z":
                        secs -= zone_seconds(zone_s)
                    nanos = secs * ns_per_sec
                    if frac_s:
                        nanos += int(frac_s) * pow10[9 - len(frac_s)]
                    with_ts += 1

                    if token is None:
                        pos = fast.end()
                        source_name = src_s
                    else:
                        found = severity_of(token)
                        if found is None:
                            # Group 5 ate an ordinary word — this log has no
                            # level field. Rewind so the body keeps its text.
                            pos = fast.start(5)
                        else:
                            level = found
                            pos = fast.end()
                            source_name = src_s
            if fast is None:
                nanos, pos = scan(line, default_year)
                if nanos:
                    with_ts += 1
                elif timestamped:
                    is_continuation = True

        if is_continuation:
            cont += 1
            if len(pending) < MAX_CONTINUATION_LINES and pending_chars < MAX_BODY_CHARS:
                text = line.strip()
                pending.append(text)
                pending_chars += len(text) + 1
            continue

        if pending:
            bodies[-1] = bodies[-1] + "\n" + "\n".join(pending)
            pending.clear()
            pending_chars = 0

        emitted += 1
        if not decided and emitted >= _PROBE_RECORDS:
            timestamped = with_ts >= _PROBE_MIN_TIMESTAMPED
            prefix_search = prefix_hits > 0
            decided = True

        # Either the combined pattern missed, or it matched but a pid sits
        # between the timestamp and the level — `... 25746 INFO nova.api ...`,
        # which is where oslo.log and Python's logging default both put one.
        # The pid is consumed only if a level really does follow it, so a
        # message opening with a number keeps its first word.
        if level == SEV_UNSPECIFIED:
            probe = pos
            match = pid_at(line, probe)
            if match is not None:
                probe = match.end()
            match = level_at(line, probe)
            if match is not None:
                found = severity_of(match.group(1))
                if found is not None:
                    level = found
                    pos = match.end()

        # The fast path recognises a bracketed logger; syslog's `tag[pid]:`,
        # bare dotted names and undelimited loggers need their own patterns.
        if source_name is None:
            match = (
                source_at(line, pos)
                or dotted_source_at(line, pos)
                or tag_pid_at(line, pos)
                or bare_source_at(line, pos)
                or host_tag_at(line, pos)
            )
            if match is not None:
                source_name = match.group(1)
                pos = match.end()

        # Some formats put the logger before the level: `api.handler INFO msg`.
        if level == SEV_UNSPECIFIED:
            match = level_at(line, pos)
            if match is not None:
                found = severity_of(match.group(1))
                if found is not None:
                    level = found
                    pos = match.end()

        source_id = unknown_id
        if source_name is not None:
            got = source_index.get(source_name)
            if got is None:
                got = len(sources)
                source_index[source_name] = got
                sources.append(source_name)
            source_id = got

        body = line[pos:] if pos else line

        if level == SEV_UNSPECIFIED:
            level = level_head(body)

        if level:
            with_sev += 1

        ts_append(nanos)
        sev_append(level)
        src_append(source_id)
        body_append(body)
        trace_append("")
        if attr_append is not None:
            attr_append({})

    if pending and bodies:
        bodies[-1] = bodies[-1] + "\n" + "\n".join(pending)

    stats.bytes_in = nbytes
    stats.lines_total = total
    stats.lines_blank = blank
    stats.lines_continuation = cont
    stats.records = len(bodies)
    stats.with_timestamp = with_ts
    stats.with_severity = with_sev


# --------------------------------------------------------------------------
# JSON lines
# --------------------------------------------------------------------------

_TS_KEYS = (
    "timeUnixNano", "observedTimeUnixNano", "timestamp", "@timestamp", "time",
    "ts", "Timestamp", "eventTime", "asctime", "date",
)
_SEV_KEYS = (
    "severityNumber", "severity_number", "severityText", "severity_text",
    "severity", "level", "levelname", "log.level", "loglevel", "levelno",
)
_BODY_KEYS = ("body", "message", "msg", "Body", "log", "text", "short_message", "event")
_SRC_KEYS = (
    "service.name", "serviceName", "service", "logger", "logger_name", "logger.name",
    "source", "component", "app", "container_name", "name",
)
_TRACE_KEYS = ("traceId", "trace_id", "trace.id", "traceID", "TraceId", "dd.trace_id")

#: Keys already consumed by a dedicated column, so they are not duplicated into
#: the attributes bag.
_ALL_KEYS = frozenset(_TS_KEYS + _SEV_KEYS + _BODY_KEYS + _SRC_KEYS + _TRACE_KEYS)


def _resolve(obj: dict[str, Any], candidates: tuple[str, ...]) -> str:
    """First candidate key present in `obj`, or "" if none is."""
    for key in candidates:
        if key in obj:
            return key
    return ""


def _coerce_severity(value: Any, from_number_field: bool) -> int:
    """Normalize whatever a structured logger calls a level onto OTel's scale.

    Numbers are ambiguous — 20 is INFO on Python's `levelno` scale and ERROR on
    OTel's — so the field name breaks the tie when it can, and the well-known
    Python constants break it when the name cannot.
    """
    if isinstance(value, str):
        got = _SEVERITY.get(value)
        if got is not None:
            return got
        return _SEVERITY.get(value.strip().upper(), SEV_UNSPECIFIED)
    if isinstance(value, bool) or not isinstance(value, int):
        return SEV_UNSPECIFIED
    if from_number_field:
        return value if 0 <= value <= 24 else SEV_UNSPECIFIED
    if value in _PYTHON_LEVELNO:
        return _PYTHON_LEVELNO[value]
    if 0 <= value <= 7:
        return _SYSLOG_PRI[value]
    if 8 <= value <= 24:
        return value
    return SEV_UNSPECIFIED


def _ingest_jsonl(batch: RecordBatch, lines: Iterable[str], default_year: int) -> None:
    stats = batch.stats
    bodies = batch.bodies
    sources = batch.sources
    source_index = batch._source_index

    ts_append = batch.timestamps.append
    sev_append = batch.severities.append
    src_append = batch.source_ids.append
    body_append = bodies.append
    trace_append = batch.trace_ids.append
    attributes = batch.attributes
    attr_append = attributes.append if attributes is not None else None

    loads = json.loads
    coerce_ts = timestamps.coerce
    unknown_id = batch.source_id(UNKNOWN_SOURCE)

    # Resolved from the first decodable object. A JSONL stream comes from one
    # producer, so the key names hold for the whole file; re-probing ten
    # candidate keys per record would dominate the loop.
    ts_key = sev_key = body_key = src_key = trace_key = ""
    resolved = False
    sev_is_number = False

    total = blank = unparsed = with_ts = with_sev = with_trace = nbytes = 0

    for raw in lines:
        total += 1
        nbytes += len(raw)
        line = raw.strip()
        if not line:
            blank += 1
            continue
        try:
            obj = loads(line)
        except ValueError:
            unparsed += 1
            continue
        if not isinstance(obj, dict):
            unparsed += 1
            continue

        if not resolved:
            ts_key = _resolve(obj, _TS_KEYS)
            sev_key = _resolve(obj, _SEV_KEYS)
            body_key = _resolve(obj, _BODY_KEYS)
            src_key = _resolve(obj, _SRC_KEYS)
            trace_key = _resolve(obj, _TRACE_KEYS)
            sev_is_number = sev_key in ("severityNumber", "severity_number")
            resolved = True

        nanos = coerce_ts(obj.get(ts_key), default_year) if ts_key else 0
        if nanos:
            with_ts += 1

        severity = SEV_UNSPECIFIED
        if sev_key:
            severity = _coerce_severity(obj.get(sev_key), sev_is_number)
            if severity:
                with_sev += 1

        body = obj.get(body_key) if body_key else None
        if not isinstance(body, str):
            # No recognised message field — keep the record rather than drop it,
            # and let templating deal with the raw JSON.
            body = line if body is None else str(body)

        source_id = unknown_id
        if src_key:
            name = obj.get(src_key)
            if isinstance(name, str) and name:
                got = source_index.get(name)
                if got is None:
                    got = len(sources)
                    source_index[name] = got
                    sources.append(name)
                source_id = got

        trace = ""
        if trace_key:
            value = obj.get(trace_key)
            if isinstance(value, str):
                trace = value
                if value:
                    with_trace += 1

        ts_append(nanos)
        sev_append(severity)
        src_append(source_id)
        body_append(body)
        trace_append(trace)
        if attr_append is not None:
            attr_append({k: v for k, v in obj.items() if k not in _ALL_KEYS})

    stats.bytes_in = nbytes
    stats.lines_total = total
    stats.lines_blank = blank
    stats.lines_unparsed = unparsed
    stats.records = len(bodies)
    stats.with_timestamp = with_ts
    stats.with_severity = with_sev
    stats.with_trace = with_trace


# --------------------------------------------------------------------------
# OTLP / JSON
# --------------------------------------------------------------------------

def _any_value(value: Any) -> Any:
    """Unwrap OTLP's `AnyValue` tagged union into a plain Python value."""
    if not isinstance(value, dict):
        return value
    for tag in ("stringValue", "boolValue", "bytesValue"):
        if tag in value:
            return value[tag]
    if "intValue" in value:
        raw = value["intValue"]
        return int(raw) if isinstance(raw, str) else raw
    if "doubleValue" in value:
        return value["doubleValue"]
    if "arrayValue" in value:
        return [_any_value(v) for v in value["arrayValue"].get("values", [])]
    if "kvlistValue" in value:
        return _attributes(value["kvlistValue"].get("values", []))
    return None


def _attributes(items: Any) -> dict[str, Any]:
    """OTLP writes attributes as a `[{key, value}]` list, not an object."""
    if not isinstance(items, list):
        return {}
    return {i["key"]: _any_value(i.get("value")) for i in items if isinstance(i, dict) and "key" in i}


def _ingest_otlp(batch: RecordBatch, text: str) -> None:
    stats = batch.stats
    stats.bytes_in = len(text)
    try:
        document = json.loads(text)
    except ValueError:
        stats.lines_unparsed = 1
        return

    resource_logs = document.get("resourceLogs") or document.get("resource_logs") or []
    coerce_ts = timestamps.coerce
    unknown_id = batch.source_id(UNKNOWN_SOURCE)
    keep_attributes = batch.attributes is not None

    total = with_ts = with_sev = with_trace = 0

    for entry in resource_logs:
        resource = _attributes((entry.get("resource") or {}).get("attributes"))
        service = resource.get("service.name")
        resource_source = batch.source_id(service) if isinstance(service, str) and service else unknown_id

        for scope_logs in entry.get("scopeLogs") or entry.get("scope_logs") or []:
            scope = scope_logs.get("scope") or {}
            scope_name = scope.get("name")
            # An instrumentation scope names the library that emitted the line,
            # which is a better "source" than the service when both exist.
            source_id = (
                batch.source_id(scope_name)
                if isinstance(scope_name, str) and scope_name
                else resource_source
            )

            for record in scope_logs.get("logRecords") or scope_logs.get("log_records") or []:
                total += 1
                nanos = coerce_ts(record.get("timeUnixNano") or record.get("time_unix_nano"))
                if not nanos:
                    nanos = coerce_ts(
                        record.get("observedTimeUnixNano")
                        or record.get("observed_time_unix_nano")
                    )
                if nanos:
                    with_ts += 1

                severity = record.get("severityNumber", record.get("severity_number"))
                if isinstance(severity, int) and 0 <= severity <= 24:
                    pass
                else:
                    severity = _coerce_severity(
                        record.get("severityText") or record.get("severity_text") or "", False
                    )
                if severity:
                    with_sev += 1

                body = _any_value(record.get("body"))
                if not isinstance(body, str):
                    body = "" if body is None else json.dumps(body, separators=(",", ":"))

                trace = record.get("traceId") or record.get("trace_id") or ""
                if trace:
                    with_trace += 1

                batch.timestamps.append(nanos)
                batch.severities.append(severity)
                batch.source_ids.append(source_id)
                batch.bodies.append(body)
                batch.trace_ids.append(trace if isinstance(trace, str) else "")
                if keep_attributes:
                    merged = dict(resource)
                    merged.update(_attributes(record.get("attributes")))
                    span = record.get("spanId") or record.get("span_id")
                    if span:
                        merged["span_id"] = span
                    batch.attributes.append(merged)  # type: ignore[union-attr]

    stats.lines_total = total
    stats.records = len(batch.bodies)
    stats.with_timestamp = with_ts
    stats.with_severity = with_sev
    stats.with_trace = with_trace


# --------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------

def ingest_lines(
    lines: Iterable[str],
    fmt: str = "text",
    keep_attributes: bool = False,
    default_year: int = 0,
    timings: Timings | None = None,
) -> RecordBatch:
    """Ingest an iterable of lines in a known format.

    `fmt` must be `text` or `jsonl` — OTLP is a single document and cannot be
    consumed line by line, so it has no streaming form.
    """
    batch = RecordBatch(keep_attributes=keep_attributes)
    handler = _ingest_jsonl if fmt == "jsonl" else _ingest_text
    if timings is not None:
        with timings.stage("ingest"):
            handler(batch, lines, default_year)
    else:
        handler(batch, lines, default_year)
    batch.stats.format = fmt
    _record_counters(batch, timings)
    return batch


def ingest_text(
    text: str,
    fmt: str = "",
    keep_attributes: bool = False,
    default_year: int = 0,
    timings: Timings | None = None,
) -> RecordBatch:
    """Ingest a whole blob — the pasted-logs path. Format is sniffed if absent."""
    fmt = fmt or sniff(text)
    batch = RecordBatch(keep_attributes=keep_attributes)

    def run() -> None:
        if fmt == "otlp":
            _ingest_otlp(batch, text)
        elif fmt == "jsonl":
            _ingest_jsonl(batch, text.splitlines(), default_year)
        else:
            _ingest_text(batch, text.splitlines(), default_year)

    if timings is not None:
        with timings.stage("ingest"):
            run()
    else:
        run()

    batch.stats.format = fmt
    if not batch.stats.bytes_in:
        batch.stats.bytes_in = len(text)
    _record_counters(batch, timings)
    return batch


def ingest_file(
    path: str | os.PathLike[str],
    fmt: str = "",
    keep_attributes: bool = False,
    default_year: int = 0,
    timings: Timings | None = None,
    encoding: str = "utf-8",
) -> RecordBatch:
    """Ingest a file.

    Text and JSONL stream line by line, so peak memory tracks the records
    produced rather than the file size. OTLP has to be read whole — it is one
    JSON document, and there is no way around that short of a streaming parser.
    """
    if not fmt:
        with open(path, "r", encoding=encoding, errors="replace") as handle:
            fmt = sniff(handle.read(65_536))

    if fmt == "otlp":
        with open(path, "r", encoding=encoding, errors="replace") as handle:
            return ingest_text(
                handle.read(),
                fmt="otlp",
                keep_attributes=keep_attributes,
                timings=timings,
            )

    batch = RecordBatch(keep_attributes=keep_attributes)
    handler = _ingest_jsonl if fmt == "jsonl" else _ingest_text
    with open(path, "r", encoding=encoding, errors="replace") as handle:
        if timings is not None:
            with timings.stage("ingest"):
                handler(batch, handle, default_year)
        else:
            handler(batch, handle, default_year)

    batch.stats.format = fmt
    _record_counters(batch, timings)
    return batch


def _record_counters(batch: RecordBatch, timings: Timings | None) -> None:
    if timings is None:
        return
    stats = batch.stats
    timings.set("records", stats.records)
    timings.set("bytes", stats.bytes_in)
    timings.set("lines", stats.lines_total)
    timings.set("sources", len(batch.sources))
