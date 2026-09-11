"""Postgres server log.

Worth its own parser for one field: the backend PID in `[1234]`. A Postgres
backend serves exactly one session, so that PID groups every statement, lock
wait and error belonging to one connection — an exact join key from a database
nobody instrumented. It is the single most useful correlation key available
from an uninstrumented stack, and it is why "select Postgres" in the upload UI
is worth asking for.

The subordinate tags (`DETAIL`, `HINT`, `STATEMENT`, `CONTEXT`, `QUERY`) are
folded into the entry above them. Postgres writes them as separate records,
but they are the failing statement and its parameters — the most diagnostic
part of the error, and useless detached from it.
"""

import re
from typing import TYPE_CHECKING, Iterator

from ingest.entry import Entry
from ingest.parsers.common import fold, is_continuation, level_from_name, parse_iso

if TYPE_CHECKING:
    from ingest.parsers import ParseContext

#: `2026-08-04 14:58:11.004 UTC [1234] user@db 5f2c1a3b.4d2 LOG:  message`
#: Everything after the PID varies with `log_line_prefix`, so it is captured
#: loosely and picked apart afterwards.
_LINE_RE = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:[.,]\d+)?)"
    r"(?:\s+(?P<zone>[A-Z]{2,5}|[+-]\d{2}(?::?\d{2})?))?"
    r"\s*\[(?P<pid>\d+)(?:-(?P<lineno>\d+))?\]\s*"
    r"(?P<prefix>.*?)"
    r"(?P<tag>LOG|ERROR|FATAL|PANIC|WARNING|NOTICE|DEBUG[1-5]?|INFO|STATEMENT|DETAIL|HINT|CONTEXT|QUERY):\s*"
    r"(?P<message>.*)$",
    re.DOTALL,
)

#: A line carrying the prefix but no recognised tag — still a Postgres record.
_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:\s+\S+)?\s*\[\d+")

#: Tags that annotate the record above them rather than standing alone.
_SUBORDINATE = frozenset({"STATEMENT", "DETAIL", "HINT", "CONTEXT", "QUERY"})

#: `user@database` in the prefix, when `log_line_prefix` includes `%u@%d`.
_USER_DB_RE = re.compile(r"\b([\w.-]+)@([\w.-]+)\b")

#: `duration: 5012.334 ms` — emitted by `log_min_duration_statement`, and the
#: closest thing Postgres gives you to a span duration.
_DURATION_RE = re.compile(r"\bduration:\s*([\d.]+)\s*ms")


def _starts_entry(line: str) -> bool:
    """True for a new record — a Postgres prefix with a non-subordinate tag."""
    match = _LINE_RE.match(line)
    if match is not None:
        return match.group("tag") not in _SUBORDINATE
    if _PREFIX_RE.match(line):
        # A prefixed line whose tag we do not recognise still starts a record.
        return True
    # No Postgres prefix at all. An indented line is a wrapped SQL body and
    # belongs to the record above; anything else is another format sharing the
    # file and gets an entry of its own rather than being swallowed.
    return not is_continuation(line)


def parse(text: str, ctx: "ParseContext") -> Iterator[Entry]:
    from ingest.parsers.plain import build_entry

    for line_no, line, extra in fold(text, _starts_entry):
        match = _LINE_RE.match(line)
        if match is None:
            yield build_entry(line, extra, line_no, ctx)
            continue

        fields = match.groupdict()
        zone = fields["zone"] or ""
        raw_time = fields["time"]
        # A numeric offset can be parsed directly; a zone abbreviation cannot
        # be resolved unambiguously (CST is three different zones), so the
        # timestamp stays naive and the file's declared timezone settles it.
        timestamp = parse_iso(f"{raw_time}{zone}" if zone[:1] in "+-" else raw_time)

        attributes: dict[str, object] = {"pg_pid": int(fields["pid"])}
        if fields["lineno"]:
            attributes["pg_session_line"] = int(fields["lineno"])

        prefix = (fields["prefix"] or "").strip()
        user_db = _USER_DB_RE.search(prefix)
        if user_db:
            attributes["db.user"] = user_db.group(1)
            attributes["db.name"] = user_db.group(2)

        message = fields["message"]
        if extra:
            message = message.rstrip() + "\n" + "\n".join(part.strip() for part in extra)

        duration = _DURATION_RE.search(message)
        if duration:
            attributes["duration_ms"] = float(duration.group(1))

        yield Entry(
            file_id=ctx.file_id,
            line_no=line_no,
            service=ctx.service,
            role=ctx.role,
            timestamp=timestamp,
            time_source="offset" if timestamp and timestamp.tzinfo else ("none" if not timestamp else "naive"),
            level=level_from_name(fields["tag"]),
            message=message.strip(),
            attributes=attributes,
            # The PID is the session. Correlation joins on it directly.
            correlation_keys={"pg_pid": fields["pid"]},
            raw="\n".join((line, *extra)) if extra else line,
        )


__all__ = ["parse"]
