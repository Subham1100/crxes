"""Render entries for an agent prompt.

A stopgap. Once the digest and correlation stages land, the agents read a
digest and a handful of reconstructed flows rather than a transcript, and this
module goes away — that is the change that takes the token count down by orders
of magnitude. Until then the existing pipeline needs its transcript, and this
builds it from the new entry shape.
"""

from ingest.entry import Entry

#: Kept in sync with the widest level name, so the columns line up and the
#: model sees a table rather than ragged text.
_LEVEL_WIDTH = 8


def render(entry: Entry) -> str:
    stamp = entry.timestamp.isoformat() if entry.timestamp else "-"
    return f"{stamp} {entry.level.upper():<{_LEVEL_WIDTH}} {entry.service}: {entry.message}"


def to_prompt(entries: list[Entry], max_chars: int = 60_000) -> str:
    """Render entries as a transcript, trimming the middle if oversized.

    The head holds the onset of an incident and the tail holds the failure, so
    when something has to go it is the repetitive middle.
    """
    body = "\n".join(render(entry) for entry in entries)
    if len(body) <= max_chars:
        return body

    half = max_chars // 2
    head, tail = body[:half], body[-half:]
    omitted = len(body) - max_chars
    return f"{head}\n\n… [{omitted:,} characters of the middle omitted] …\n\n{tail}"


__all__ = ["render", "to_prompt"]
