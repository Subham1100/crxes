"""Syslog, both dialects.

RFC 5424 is the modern one: a priority, a version digit, a real ISO timestamp
and structured data. RFC 3164 is what most daemons still emit, and it is
lossy by design — no year, no timezone, no structure. The year has to be
supplied from context and the zone from the file's declared timezone, which is
why `SourceFile.timezone` matters most for this format.
"""

import re
from typing import TYPE_CHECKING, Iterator

from ingest.entry import Entry
from ingest.parsers.common import (
    fold,
    is_continuation,
    level_from_syslog_priority,
    level_from_text,
    parse_iso,
    parse_syslog_ts,
)

if TYPE_CHECKING:
    from ingest.parsers import ParseContext

_RFC5424_RE = re.compile(
    r"^<(?P<pri>\d{1,3})>1 "
    r"(?P<time>\S+) (?P<host>\S+) (?P<app>\S+) (?P<procid>\S+) (?P<msgid>\S+) "
    r"(?P<rest>.*)$",
    re.DOTALL,
)

_RFC3164_RE = re.compile(
    r"^(?:<(?P<pri>\d{1,3})>)?"
    r"(?P<time>[A-Z][a-z]{2}\s+\d{1,2} \d{2}:\d{2}:\d{2}) "
    r"(?P<host>\S+) "
    r"(?P<tag>[^\s:\[]+)(?:\[(?P<pid>\d+)\])?: ?"
    r"(?P<message>.*)$",
    re.DOTALL,
)

#: RFC 5424 structured data: `[id key="value" ...][id2 ...]`
_SD_ELEMENT_RE = re.compile(r'\[([^\s\]]+)((?:\s+[\w.@-]+="(?:[^"\\]|\\.)*")*)\]')
_SD_PARAM_RE = re.compile(r'([\w.@-]+)="((?:[^"\\]|\\.)*)"')


def _parse_structured_data(rest: str) -> tuple[dict[str, str], str]:
    """Split RFC 5424 structured data from the message that follows it."""
    if rest.startswith("-"):
        return {}, rest[1:].lstrip()
    if not rest.startswith("["):
        return {}, rest

    params: dict[str, str] = {}
    cursor = 0
    for match in _SD_ELEMENT_RE.finditer(rest):
        if match.start() != cursor:
            break
        sd_id = match.group(1)
        for param in _SD_PARAM_RE.finditer(match.group(2) or ""):
            params[f"{sd_id}.{param.group(1)}"] = param.group(2).replace('\\"', '"')
        cursor = match.end()
    return params, rest[cursor:].lstrip()


def _correlation_from(app: str, procid: str, params: dict[str, str]) -> dict[str, str]:
    correlation: dict[str, str] = {}
    if procid and procid != "-":
        correlation["pid"] = procid
    for key, value in params.items():
        short = key.rsplit(".", 1)[-1].lower()
        if short in ("request_id", "requestid", "req_id", "correlation_id", "session_id", "user_id"):
            correlation[short] = value
    return correlation


def parse_5424(text: str, ctx: "ParseContext") -> Iterator[Entry]:
    from ingest.parsers.plain import build_entry

    for line_no, line, extra in fold(
        text, lambda candidate: bool(_RFC5424_RE.match(candidate)) or not is_continuation(candidate)
    ):
        match = _RFC5424_RE.match(line)
        if match is None:
            yield build_entry(line, extra, line_no, ctx)
            continue

        fields = match.groupdict()
        params, message = _parse_structured_data(fields["rest"])
        timestamp = parse_iso(fields["time"]) if fields["time"] != "-" else None

        app = fields["app"] if fields["app"] != "-" else ""
        service = ctx.service if ctx.service_declared or not app else app

        attributes: dict[str, object] = dict(params)
        if fields["host"] != "-":
            attributes["host"] = fields["host"]
        if fields["msgid"] != "-":
            attributes["msgid"] = fields["msgid"]
        if app and ctx.service_declared:
            attributes["logger"] = app

        raw = "\n".join((line, *extra)) if extra else line
        if extra:
            message = message + "\n" + "\n".join(part.strip() for part in extra)

        yield Entry(
            file_id=ctx.file_id,
            line_no=line_no,
            service=service,
            role=ctx.role,
            timestamp=timestamp,
            time_source="offset" if timestamp and timestamp.tzinfo else ("none" if not timestamp else "naive"),
            level=level_from_syslog_priority(int(fields["pri"])),
            message=message.strip(),
            attributes=attributes,
            correlation_keys=_correlation_from(app, fields["procid"], params),
            raw=raw,
        )


def parse_3164(text: str, ctx: "ParseContext") -> Iterator[Entry]:
    from ingest.parsers.plain import build_entry

    year = ctx.reference_year
    for line_no, line, extra in fold(
        text, lambda candidate: bool(_RFC3164_RE.match(candidate)) or not is_continuation(candidate)
    ):
        match = _RFC3164_RE.match(line)
        if match is None:
            yield build_entry(line, extra, line_no, ctx)
            continue

        fields = match.groupdict()
        # Naive by construction — RFC 3164 has no zone. `normalize` resolves it
        # against the file's declared timezone.
        timestamp = parse_syslog_ts(fields["time"], year) if year else None

        pri = fields["pri"]
        if pri is not None:
            level = level_from_syslog_priority(int(pri))
        else:
            # No priority survived the collector. The message's own severity
            # word is the only signal left.
            level, _, _ = level_from_text(fields["message"][:80])

        tag = fields["tag"] or ""
        service = ctx.service if ctx.service_declared or not tag else tag

        attributes: dict[str, object] = {"host": fields["host"]}
        if tag and ctx.service_declared:
            attributes["logger"] = tag

        correlation: dict[str, str] = {}
        if fields["pid"]:
            correlation["pid"] = fields["pid"]

        message = fields["message"]
        raw = "\n".join((line, *extra)) if extra else line
        if extra:
            message = message + "\n" + "\n".join(part.strip() for part in extra)

        yield Entry(
            file_id=ctx.file_id,
            line_no=line_no,
            service=service,
            role=ctx.role,
            timestamp=timestamp,
            time_source="none" if timestamp is None else "naive",
            level=level,
            message=message.strip(),
            attributes=attributes,
            correlation_keys=correlation,
            raw=raw,
        )


__all__ = ["parse_3164", "parse_5424"]
