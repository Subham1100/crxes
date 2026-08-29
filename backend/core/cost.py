"""What a pipeline run costs — forecast before it runs, and priced after.

The forecast is not "tokens × rate". The pipeline makes four sequential calls
whose prompts are built out of each other (`agents.pipeline.run_pipeline`), so
the log sample is billed once, at the parser, while the three later agents pay
for a prompt that grows as their predecessors write. This module walks the same
four stages the pipeline does and prices each one.

Two of the inputs are genuinely unknowable in advance — how much prose each
agent will write, and how much it will think before writing it — so those come
from the calibration constants below. Everything else (the log sample, the
system prompts, the wrapper text, the fan-in of prior outputs) is computed from
the exact strings the pipeline will send.
"""

from dataclasses import dataclass

from agents.pipeline import AGENTS, LOG_PROMPT_TEMPLATE, SECTION_SEPARATOR, SECTION_TEMPLATE
from config import settings
from core import pricing, tokens
from core.pricing import ModelPrice

#: Visible output per agent, in Claude tokens. Each prompt asks for a fixed set
#: of Markdown sections (or, for the predictor, up to six JSON predictions), so
#: the length is governed by the prompt rather than by the input — except for
#: the parser, which enumerates distinct events and so grows with the sample.
BASE_OUTPUT_TOKENS = {
    "parser": 600,
    "pattern": 1_000,
    "rootcause": 1_300,
    "predictor": 1_200,
}

#: Extra parser output per token of log sample, and the ceiling it saturates
#: at once event grouping has collapsed the repetitive bulk.
PARSER_OUTPUT_PER_LOG_TOKEN = 0.05
PARSER_OUTPUT_CEILING = 4_000

#: Adaptive thinking bills reasoning as output tokens at the output rate, and
#: on a four-agent analysis it is the larger half of the bill. As a multiple of
#: the visible output, by `output_config.effort`.
THINKING_MULTIPLIER = {
    "low": 0.6,
    "medium": 1.2,
    "high": 2.2,
    "xhigh": 3.2,
    "max": 4.5,
}
DEFAULT_THINKING_MULTIPLIER = THINKING_MULTIPLIER["medium"]

#: Per-request framing the API adds around the messages themselves.
MESSAGE_OVERHEAD_TOKENS = 12


@dataclass(frozen=True)
class StageEstimate:
    """One agent's share of the forecast."""

    key: str
    name: str
    input_tokens: int
    output_tokens: int  # visible output plus thinking
    thinking_tokens: int


@dataclass(frozen=True)
class TokenEstimate:
    """The whole run, in Claude tokens."""

    stages: tuple[StageEstimate, ...]
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    log_tokens: int
    effort: str
    #: True when the log sample was counted by the API rather than estimated
    #: from its character count.
    counted: bool

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def largest_prompt_tokens(self) -> int:
        """The biggest single call — what has to fit in a context window."""
        return max(s.input_tokens + s.output_tokens for s in self.stages)


@dataclass(frozen=True)
class ModelEstimate:
    """What one model would charge for the run described by a `TokenEstimate`."""

    id: str
    provider: str
    provider_label: str
    label: str
    input_per_mtok: float
    output_per_mtok: float
    input_tokens: int
    output_tokens: int
    input_usd: float
    output_usd: float
    total_usd: float
    context_window: int
    fits_context: bool
    #: True for the model the pipeline is configured to actually run.
    is_current: bool


def _system_tokens(system: str) -> int:
    return tokens.estimate_prose_tokens(system)


def _visible_output(key: str, log_tokens: int) -> int:
    base = BASE_OUTPUT_TOKENS[key]
    if key != "parser":
        return base
    return min(round(base + log_tokens * PARSER_OUTPUT_PER_LOG_TOKEN), PARSER_OUTPUT_CEILING)


def estimate_pipeline_tokens(
    log_tokens: int, counted: bool = False, effort: str | None = None
) -> TokenEstimate:
    """Forecast the four calls for a sample worth `log_tokens` Claude tokens."""
    effort = effort or settings.anthropic_effort
    thinking_multiplier = THINKING_MULTIPLIER.get(effort, DEFAULT_THINKING_MULTIPLIER)

    # The wrapper the parser's sample arrives in, minus the sample itself.
    wrapper_tokens = tokens.estimate_prose_tokens(LOG_PROMPT_TEMPLATE.format(log_text=""))
    # The "## {name} output" heading and the blank line joining each section.
    header_tokens = [
        tokens.estimate_prose_tokens(
            SECTION_TEMPLATE.format(agent_name=name, agent_output="") + SECTION_SEPARATOR
        )
        for _, name, _ in AGENTS
    ]

    stages: list[StageEstimate] = []
    visible: dict[str, int] = {}

    for index, (key, name, system) in enumerate(AGENTS):
        if index == 0:
            # Only the parser pays for the raw sample.
            user_tokens = wrapper_tokens + log_tokens
        else:
            # Every earlier agent's prose, each under its own heading.
            user_tokens = sum(
                header_tokens[i] + visible[AGENTS[i][0]] for i in range(index)
            )

        out = _visible_output(key, log_tokens)
        visible[key] = out
        thinking = round(out * thinking_multiplier)

        stages.append(
            StageEstimate(
                key=key,
                name=name,
                input_tokens=_system_tokens(system) + user_tokens + MESSAGE_OVERHEAD_TOKENS,
                output_tokens=out + thinking,
                thinking_tokens=thinking,
            )
        )

    return TokenEstimate(
        stages=tuple(stages),
        input_tokens=sum(s.input_tokens for s in stages),
        output_tokens=sum(s.output_tokens for s in stages),
        thinking_tokens=sum(s.thinking_tokens for s in stages),
        log_tokens=log_tokens,
        effort=effort,
        counted=counted,
    )


def price_model(estimate: TokenEstimate, model: ModelPrice) -> ModelEstimate:
    """Price one catalog entry against a token forecast."""
    scaled_in = round(estimate.input_tokens * model.token_ratio)
    scaled_out = round(estimate.output_tokens * model.token_ratio)
    input_usd = scaled_in * model.input_per_mtok / 1_000_000
    output_usd = scaled_out * model.output_per_mtok / 1_000_000

    return ModelEstimate(
        id=model.id,
        provider=model.provider,
        provider_label=pricing.PROVIDER_LABELS.get(model.provider, model.provider),
        label=model.label,
        input_per_mtok=model.input_per_mtok,
        output_per_mtok=model.output_per_mtok,
        input_tokens=scaled_in,
        output_tokens=scaled_out,
        input_usd=input_usd,
        output_usd=output_usd,
        total_usd=input_usd + output_usd,
        context_window=model.context_window,
        fits_context=round(estimate.largest_prompt_tokens * model.token_ratio)
        <= model.context_window,
        is_current=model.id == settings.anthropic_model,
    )


def price_catalog(estimate: TokenEstimate) -> list[ModelEstimate]:
    """Price every model in the catalog, cheapest first.

    The configured model leads regardless of price — it is the number that is
    actually going to be spent, and the rest are the comparison.
    """
    priced = [price_model(estimate, model) for model in pricing.CATALOG]
    return sorted(priced, key=lambda m: (not m.is_current, m.total_usd))


def actual_cost_usd(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """Cost of a finished run from its reported usage. None if unpriced."""
    model = pricing.get(model_id)
    if model is None:
        return None
    return pricing.cost_usd(
        model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens
    )
