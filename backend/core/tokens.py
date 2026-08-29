"""Token counting for cost estimates.

Counting is model-specific, so this counts against the Claude model the
pipeline actually runs (`settings.anthropic_model`) and `core.pricing` scales
that count for other providers. `count_tokens` is a free, non-billed endpoint —
but it is still a network round trip, so `estimate_tokens` is the offline
fallback for when the key is unset or the API is unreachable, and callers are
told which one produced the number.
"""

import logging
from functools import lru_cache

import anthropic
from anthropic import AsyncAnthropic

from config import settings

log = logging.getLogger(__name__)

#: Claude tokens per character of *log* text. Prose runs closer to 4.0; log
#: lines are denser — timestamps, hex IDs, paths and stack frames all tokenize
#: into more pieces per character than English does.
CHARS_PER_TOKEN = 3.4

#: Claude tokens per character of ordinary prose — the system prompts, and the
#: Markdown the agents write back. Looser than log text.
PROSE_CHARS_PER_TOKEN = 3.9

#: The estimate endpoint runs while someone waits on a textarea. If counting
#: takes longer than this, the heuristic is the better answer.
COUNT_TIMEOUT_SECONDS = 8.0


@lru_cache
def _client() -> AsyncAnthropic | None:
    if not settings.anthropic_api_key:
        return None
    return AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=COUNT_TIMEOUT_SECONDS)


def estimate_tokens(text: str) -> int:
    """Offline character-ratio estimate. Good to roughly ±15% on log text."""
    return round(len(text) / CHARS_PER_TOKEN)


def estimate_prose_tokens(text: str) -> int:
    """As `estimate_tokens`, for prompt and Markdown text rather than logs."""
    return round(len(text) / PROSE_CHARS_PER_TOKEN)


async def count_tokens(text: str, system: str = "") -> tuple[int, bool]:
    """Count `text` as Claude would.

    Returns the count and whether it came from the API — False means the
    character heuristic produced it and the caller should say so.
    """
    client = _client()
    if client is None or not text:
        return estimate_tokens(text) + estimate_tokens(system), False

    kwargs: dict = {
        "model": settings.anthropic_model,
        "messages": [{"role": "user", "content": text}],
    }
    if system:
        kwargs["system"] = system

    try:
        response = await client.messages.count_tokens(**kwargs)
    except (anthropic.APIStatusError, anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
        # A cost estimate is not worth failing a request over.
        log.info("count_tokens unavailable, falling back to the heuristic: %s", exc)
        return estimate_tokens(text) + estimate_tokens(system), False

    return response.input_tokens, True
