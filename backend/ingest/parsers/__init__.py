"""Stage 3 — one parser per format, all emitting `Entry`.

Adding support for a new log format means adding a module here and a line to
`PARSERS`. Nothing downstream changes, because nothing downstream has ever seen
anything but an `Entry`.
"""

from dataclasses import dataclass
from typing import Callable, Iterator

from ingest.entry import Entry
from ingest.parsers import clf, cri, jsonl, logfmt, plain, postgres, syslog


@dataclass(slots=True)
class ParseContext:
    """What a parser knows about the file it is reading."""

    file_id: str
    #: Service label for every entry from this file.
    service: str
    role: str
    #: True when the *user* named the service, false when it was guessed from
    #: the filename. Parsers that can read a service name out of the log itself
    #: (a bracketed logger, a JSON `service` field) defer to a declared name
    #: and override a guessed one — a paste of mixed output has no per-file
    #: label to rely on, and the log's own naming is all there is.
    service_declared: bool = False
    #: Year for formats that omit it (RFC 3164). See `normalize.reference_year`.
    reference_year: int = 0
    #: JSON producer, from detection. Ignored by every other parser.
    flavor: str | None = None


Parser = Callable[[str, ParseContext], Iterator[Entry]]

PARSERS: dict[str, Parser] = {
    "plain": plain.parse,
    "json": jsonl.parse,
    "logfmt": logfmt.parse,
    "clf": clf.parse,
    "syslog": syslog.parse_3164,
    "syslog5424": syslog.parse_5424,
    "postgres": postgres.parse,
    "cri": cri.parse,
}


def parse(text: str, fmt: str, ctx: ParseContext) -> Iterator[Entry]:
    """Parse `text` as `fmt`.

    An unknown format falls back to plain rather than raising: detection can
    only ever return a known format, so an unknown one means a stale user
    override, and losing the file over it would be the wrong trade.
    """
    return PARSERS.get(fmt, plain.parse)(text, ctx)


__all__ = ["PARSERS", "ParseContext", "Parser", "parse"]
