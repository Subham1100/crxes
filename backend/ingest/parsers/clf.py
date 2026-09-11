"""Common and Combined Log Format — nginx, Apache, ALB access logs.

Access logs are the highest-value uninstrumented source there is: they sit at
the front of the request path, they carry status and latency for every request,
and proxies routinely stamp a request ID into them. That last field is the join
key that makes a gateway↔backend correlation exact rather than inferred, so it
is hunted for here in every spelling that shows up.

There is no severity in this format. It is derived from the status code, which
is the only failure signal an access log has.
"""

import re
from typing import TYPE_CHECKING, Iterator

from ingest.entry import Entry
from ingest.parsers.common import fold, is_continuation, parse_clf

if TYPE_CHECKING:
    from ingest.parsers import ParseContext

_CLF_RE = re.compile(
    r"^(?P<host>\S+) (?P<ident>\S+) (?P<user>\S+) "
    r"\[(?P<time>[^\]]+)\] "
    r'"(?P<request>[^"]*)" '
    r"(?P<status>\d{3}) (?P<size>\S+)"
    r'(?: "(?P<referer>[^"]*)" "(?P<agent>[^"]*)")?'
    r"(?P<tail>.*)$"
)

#: nginx `$request_time` / `$upstream_response_time`, appended by most custom
#: formats. Seconds with a fractional part, or bare milliseconds.
_DURATION_RE = re.compile(r"\b(?:rt|request_time|duration|took)[=:]?\s*(\d+\.?\d*)")

#: A request ID appended to the line by a proxy. Bare UUIDs and hex blobs are
#: matched too, since nginx's `$request_id` is written without a key.
_REQUEST_ID_RE = re.compile(
    r"\b(?:request[_-]?id|req[_-]?id|x[_-]request[_-]id|trace[_-]?id|correlation[_-]?id)"
    r"[=:]?\s*\"?([A-Za-z0-9._-]{6,128})\"?",
    re.IGNORECASE,
)
_BARE_ID_RE = re.compile(r"\b([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b")


def _level_for(status: int) -> str:
    if status >= 500:
        return "error"
    if status >= 400:
        return "warn"
    return "info"


def _starts_entry(line: str) -> bool:
    """A new record is an access-log line — or any unindented foreign line.

    The second half matters: nginx writes its error_log in a different format,
    and files carrying both are routine. Testing only for the CLF prefix would
    fold every error line into the access-log entry above it, which is exactly
    the entry it has nothing to do with.
    """
    if _CLF_RE.match(line):
        return True
    return not is_continuation(line)


def parse(text: str, ctx: "ParseContext") -> Iterator[Entry]:
    from ingest.parsers.plain import build_entry

    for line_no, line, extra in fold(text, _starts_entry):
        match = _CLF_RE.match(line)
        if match is None:
            # A line that is not access-log shaped — an nginx error_log entry
            # sharing the file, most often. Parse it as text rather than lose it.
            yield build_entry(line, extra, line_no, ctx)
            continue

        fields = match.groupdict()
        status = int(fields["status"])
        request = fields["request"] or ""
        parts = request.split(" ")
        method = parts[0] if parts else ""
        path = parts[1] if len(parts) > 1 else ""
        protocol = parts[2] if len(parts) > 2 else ""

        attributes: dict[str, object] = {
            "http.method": method,
            "http.path": path,
            "http.status": status,
            "client.ip": fields["host"],
        }
        if protocol:
            attributes["http.protocol"] = protocol
        if fields["size"] not in ("-", None):
            try:
                attributes["http.response_bytes"] = int(fields["size"])
            except ValueError:
                pass
        if fields["referer"]:
            attributes["http.referer"] = fields["referer"]
        if fields["agent"]:
            attributes["http.user_agent"] = fields["agent"]
        if fields["user"] not in ("-", None):
            attributes["http.user"] = fields["user"]

        tail = fields["tail"] or ""
        duration = _DURATION_RE.search(tail)
        if duration:
            # nginx writes seconds; anything over a thousand is already ms.
            value = float(duration.group(1))
            attributes["duration_ms"] = value * 1000 if value < 1000 else value

        correlation: dict[str, str] = {}
        request_id = _REQUEST_ID_RE.search(tail) or _BARE_ID_RE.search(tail)
        if request_id:
            correlation["request_id"] = request_id.group(1)

        raw = "\n".join((line, *extra)) if extra else line
        # CLF always writes a numeric UTC offset, so a parsed timestamp is
        # always aware — but a malformed one still has to read as "none".
        timestamp = parse_clf(fields["time"])
        yield Entry(
            file_id=ctx.file_id,
            line_no=line_no,
            service=ctx.service,
            role=ctx.role,
            timestamp=timestamp,
            time_source="offset" if timestamp else "none",
            level=_level_for(status),
            message=f"{method} {path} → {status}".strip(),
            attributes=attributes,
            correlation_keys=correlation,
            raw=raw,
        )


__all__ = ["parse"]
