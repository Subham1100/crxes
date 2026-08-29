"""Cost estimate request/response models.

The wire shape mirrors `core.cost` — see that module for how the numbers are
derived and which of them are calibrated rather than computed.
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from core import logs, pricing

if TYPE_CHECKING:
    # Type-only: `core.cost` reaches into `agents.pipeline`, which imports this
    # package back. Nothing here needs those classes at runtime.
    from core import cost


class EstimateRequest(BaseModel):
    logs: str = Field(min_length=1, max_length=logs.MAX_BYTES)


class StageCostOut(BaseModel):
    key: str
    name: str
    input_tokens: int
    output_tokens: int
    thinking_tokens: int

    @classmethod
    def of(cls, stage: "cost.StageEstimate") -> "StageCostOut":
        return cls(
            key=stage.key,
            name=stage.name,
            input_tokens=stage.input_tokens,
            output_tokens=stage.output_tokens,
            thinking_tokens=stage.thinking_tokens,
        )


class ModelCostOut(BaseModel):
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
    is_current: bool

    @classmethod
    def of(cls, m: "cost.ModelEstimate") -> "ModelCostOut":
        return cls(**m.__dict__)


class CostEstimateOut(BaseModel):
    """What the four-agent pipeline would cost for a given paste."""

    # What the parse found, so the caller can show the estimate without a
    # second round trip.
    log_line_count: int
    dropped_lines: int
    raw_size_bytes: int
    prompt_chars: int

    # Claude-tokenizer counts; per-model figures live on each `models` row.
    log_tokens: int
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    total_tokens: int
    largest_prompt_tokens: int
    #: "counted" — the API tokenized the sample; "estimated" — character ratio.
    token_source: str

    effort: str
    prices_updated: str
    stages: list[StageCostOut]
    models: list[ModelCostOut]

    @classmethod
    def of(
        cls,
        estimate: "cost.TokenEstimate",
        models: list["cost.ModelEstimate"],
        *,
        log_line_count: int,
        dropped_lines: int,
        raw_size_bytes: int,
        prompt_chars: int,
    ) -> "CostEstimateOut":
        return cls(
            log_line_count=log_line_count,
            dropped_lines=dropped_lines,
            raw_size_bytes=raw_size_bytes,
            prompt_chars=prompt_chars,
            log_tokens=estimate.log_tokens,
            input_tokens=estimate.input_tokens,
            output_tokens=estimate.output_tokens,
            thinking_tokens=estimate.thinking_tokens,
            total_tokens=estimate.total_tokens,
            largest_prompt_tokens=estimate.largest_prompt_tokens,
            token_source="counted" if estimate.counted else "estimated",
            effort=estimate.effort,
            prices_updated=pricing.PRICES_UPDATED,
            stages=[StageCostOut.of(s) for s in estimate.stages],
            models=[ModelCostOut.of(m) for m in models],
        )
