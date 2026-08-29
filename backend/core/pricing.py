"""Published per-token prices for the models the pipeline could run on.

This is a hand-maintained catalog, not a live feed — every provider publishes
prices on a web page, none of them on an API. `PRICES_UPDATED` is the date the
numbers below were last checked; re-check it before quoting a figure to anyone
outside the team, and update both the rate and that date together.

Only the Anthropic rows describe what crxes actually spends. The rest exist so
the estimate can answer "what would this same analysis cost elsewhere" — see
`token_ratio` for why that comparison is approximate.
"""

from dataclasses import dataclass

#: Date the rates below were last reconciled against the providers' pricing pages.
PRICES_UPDATED = "2026-08-30"

#: Standard multipliers on the input rate for Anthropic prompt caching. The
#: pipeline does not cache today (each agent sees a different prefix), so these
#: only matter once it does.
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25


@dataclass(frozen=True)
class ModelPrice:
    """List price for one model, in USD per million tokens."""

    id: str
    provider: str
    label: str
    input_per_mtok: float
    output_per_mtok: float
    context_window: int
    #: How many tokens this model's tokenizer produces per token that Claude's
    #: produces, on log-shaped text. Token counts are only ever exact for the
    #: model that counted them: we count with Claude (`core.tokens`) and scale.
    #: OpenAI's o200k tokenizer runs ~15% under Claude's on typical text, which
    #: is the same gap that makes tiktoken the wrong tool for pricing Claude.
    token_ratio: float = 1.0
    #: Where the rate came from, so the next person to update it knows where to look.
    source: str = ""


#: Ordered cheapest-first within each provider. `crxes` runs the first row.
CATALOG: tuple[ModelPrice, ...] = (
    # ── Anthropic ────────────────────────────────────────────────────────────
    ModelPrice(
        id="claude-opus-5",
        provider="anthropic",
        label="Claude Opus 5",
        input_per_mtok=5.00,
        output_per_mtok=25.00,
        context_window=1_000_000,
        source="https://claude.com/pricing",
    ),
    ModelPrice(
        id="claude-sonnet-5",
        provider="anthropic",
        label="Claude Sonnet 5",
        input_per_mtok=2.00,
        output_per_mtok=10.00,
        context_window=1_000_000,
        source="https://claude.com/pricing",
    ),
    ModelPrice(
        id="claude-haiku-4-5",
        provider="anthropic",
        label="Claude Haiku 4.5",
        input_per_mtok=1.00,
        output_per_mtok=5.00,
        context_window=200_000,
        source="https://claude.com/pricing",
    ),
    ModelPrice(
        id="claude-fable-5",
        provider="anthropic",
        label="Claude Fable 5",
        input_per_mtok=10.00,
        output_per_mtok=50.00,
        context_window=1_000_000,
        source="https://claude.com/pricing",
    ),
    # ── OpenAI ───────────────────────────────────────────────────────────────
    ModelPrice(
        id="gpt-5",
        provider="openai",
        label="GPT-5",
        input_per_mtok=1.25,
        output_per_mtok=10.00,
        context_window=400_000,
        token_ratio=0.85,
        source="https://openai.com/api/pricing/",
    ),
    ModelPrice(
        id="gpt-5-mini",
        provider="openai",
        label="GPT-5 mini",
        input_per_mtok=0.25,
        output_per_mtok=2.00,
        context_window=400_000,
        token_ratio=0.85,
        source="https://openai.com/api/pricing/",
    ),
    ModelPrice(
        id="gpt-5-nano",
        provider="openai",
        label="GPT-5 nano",
        input_per_mtok=0.05,
        output_per_mtok=0.40,
        context_window=400_000,
        token_ratio=0.85,
        source="https://openai.com/api/pricing/",
    ),
    ModelPrice(
        id="gpt-4.1",
        provider="openai",
        label="GPT-4.1",
        input_per_mtok=2.00,
        output_per_mtok=8.00,
        context_window=1_047_576,
        token_ratio=0.85,
        source="https://openai.com/api/pricing/",
    ),
    # ── Google ───────────────────────────────────────────────────────────────
    ModelPrice(
        id="gemini-2.5-pro",
        provider="google",
        label="Gemini 2.5 Pro",
        # Google charges a higher rate above a 200k-token prompt; the pipeline's
        # largest single call stays well under that, so the base rate applies.
        input_per_mtok=1.25,
        output_per_mtok=10.00,
        context_window=1_048_576,
        token_ratio=0.88,
        source="https://ai.google.dev/gemini-api/docs/pricing",
    ),
    ModelPrice(
        id="gemini-2.5-flash",
        provider="google",
        label="Gemini 2.5 Flash",
        input_per_mtok=0.30,
        output_per_mtok=2.50,
        context_window=1_048_576,
        token_ratio=0.88,
        source="https://ai.google.dev/gemini-api/docs/pricing",
    ),
    # ── Open-weight models, at a representative host ─────────────────────────
    ModelPrice(
        id="llama-3.3-70b",
        provider="meta",
        label="Llama 3.3 70B (Together)",
        input_per_mtok=0.88,
        output_per_mtok=0.88,
        context_window=131_072,
        token_ratio=0.92,
        source="https://www.together.ai/pricing",
    ),
    ModelPrice(
        id="deepseek-v3",
        provider="deepseek",
        label="DeepSeek-V3",
        input_per_mtok=0.27,
        output_per_mtok=1.10,
        context_window=128_000,
        token_ratio=0.92,
        source="https://api-docs.deepseek.com/quick_start/pricing",
    ),
)

_BY_ID = {m.id: m for m in CATALOG}

#: Display names for the `provider` field, for the UI's grouping.
PROVIDER_LABELS = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "google": "Google",
    "meta": "Meta",
    "deepseek": "DeepSeek",
}


def get(model_id: str) -> ModelPrice | None:
    """The catalog entry for `model_id`, or None if it is not priced here."""
    return _BY_ID.get(model_id)


def cost_usd(
    model: ModelPrice,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Dollar cost of one run at this model's rates.

    Token counts are Claude-tokenizer counts; `token_ratio` converts them to
    what the target model would have charged for. For Anthropic models the
    ratio is 1.0 and the arithmetic is exact.
    """
    billed_in = (
        input_tokens
        + cache_read_tokens * CACHE_READ_MULTIPLIER
        + cache_write_tokens * CACHE_WRITE_MULTIPLIER
    ) * model.token_ratio
    billed_out = output_tokens * model.token_ratio
    return (billed_in * model.input_per_mtok + billed_out * model.output_per_mtok) / 1_000_000
