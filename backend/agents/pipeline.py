"""The four-agent pipeline: Log Parser → Pattern Detector → Root Cause → Predictor.

Phase 1 runs this inline in the request. Phase 3 moves it onto Celery and
streams the same per-agent steps over SSE, so the callback below exists to give
that phase a seam it can already write into.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import anthropic
from anthropic import AsyncAnthropic, transform_schema
from pydantic import BaseModel, ValidationError

from agents import prompts
from config import settings
from exceptions import PipelineError
from schemas.agents import PredictorOutput

log = logging.getLogger(__name__)

#: Execution order, and the `AGENTS` contract shared with the frontend
#: (frontend/src/lib/types.ts) — keep the keys and indices in sync.
AGENTS: tuple[tuple[str, str, str], ...] = (
    ("parser", "Log Parser", prompts.PARSER),
    ("pattern", "Pattern Detector", prompts.PATTERN),
    ("rootcause", "Root Cause Analyzer", prompts.ROOTCAUSE),
    ("predictor", "Bug Predictor", prompts.PREDICTOR),
)

MAX_PREDICTIONS = 6

#: How the raw sample reaches the Log Parser, and how each later agent receives
#: its predecessors' prose. `core.cost` builds its estimate from these same two
#: templates, so a change here changes the estimate with it.
LOG_PROMPT_TEMPLATE = "Here is the log sample to analyze:\n\n```\n{log_text}\n```"
SECTION_TEMPLATE = "## {agent_name} output\n\n{agent_output}"
SECTION_SEPARATOR = "\n\n"


@dataclass
class PipelineResult:
    outputs: dict[str, str] = field(default_factory=dict)
    predictions: list[dict[str, Any]] = field(default_factory=list)
    tokens_used: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def usage(self) -> dict[str, int]:
        """The token counts, in the shape `core.cost.actual_cost_usd` takes."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }


#: Called after each agent finishes: (index, key, name, output). Phase 3 uses
#: this to publish `agent_done` events; Phase 1 uses it to checkpoint the row.
ProgressHook = Callable[[int, str, str, str], Awaitable[None]]


def _client() -> AsyncAnthropic:
    if not settings.anthropic_api_key:
        raise PipelineError("parser", "ANTHROPIC_API_KEY is not configured on the server")
    # Four sequential Opus calls; the SDK's 10-minute default would cut off a
    # long run partway through the pipeline.
    return AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=settings.anthropic_timeout)


def _text_of(response: Any) -> str:
    return "\n".join(block.text for block in response.content if block.type == "text").strip()


async def _call(
    client: AsyncAnthropic,
    agent_key: str,
    system: str,
    user: str,
    output_format: type[BaseModel] | None = None,
):
    output_config: dict[str, Any] = {"effort": settings.anthropic_effort}
    if output_format is not None:
        # `transform_schema` is what messages.parse() sends: it drops the
        # keywords structured outputs rejects (numeric bounds and the like)
        # into descriptions the model can still read.
        output_config["format"] = {
            "type": "json_schema",
            "schema": transform_schema(output_format),
        }

    try:
        return await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=settings.anthropic_max_tokens,
            thinking={"type": "adaptive"},
            output_config=output_config,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except anthropic.APIStatusError as exc:
        log.warning("agent %s failed: %s %s", agent_key, exc.status_code, exc.message)
        raise PipelineError(agent_key, f"Claude API error ({exc.status_code}): {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise PipelineError(agent_key, "Could not reach the Claude API") from exc


def _parse_predictions(raw: str) -> list[dict[str, Any]]:
    """Validate the predictor's JSON into rows the Prediction model accepts."""
    try:
        parsed = PredictorOutput.model_validate_json(raw)
    except ValidationError as exc:
        raise PipelineError("predictor", "Bug Predictor returned malformed JSON") from exc
    return parsed.rows(MAX_PREDICTIONS)


async def run_pipeline(log_text: str, on_agent_done: ProgressHook | None = None) -> PipelineResult:
    """Run all four agents in sequence.

    Only the Log Parser sees the raw logs; each later agent gets the prose its
    predecessors produced, which is what keeps a 60k-character paste from being
    re-sent four times.
    """
    client = _client()
    result = PipelineResult()
    try:
        return await _run(client, result, log_text, on_agent_done)
    except PipelineError as exc:
        # The agents that finished before the failure were still billed.
        exc.usage = result.usage()
        raise


async def _run(
    client: AsyncAnthropic,
    result: PipelineResult,
    log_text: str,
    on_agent_done: ProgressHook | None,
) -> PipelineResult:
    """The loop itself, split out so `run_pipeline` can annotate a failure."""
    sections: list[str] = [LOG_PROMPT_TEMPLATE.format(log_text=log_text)]

    for index, (key, name, system) in enumerate(AGENTS):
        if index > 0:
            # Hand forward every prior agent's output, labelled.
            sections = []
            for i in range(index):
                agent_name = AGENTS[i][1]
                agent_key = AGENTS[i][0]
                agent_output = result.outputs[agent_key]

                section = SECTION_TEMPLATE.format(
                    agent_name=agent_name, agent_output=agent_output
                )
                sections.append(section)

        user = SECTION_SEPARATOR.join(sections)
        output_format = PredictorOutput if key == "predictor" else None
        response = await _call(client, key, system, user, output_format=output_format)

        # Recorded before the checks below: a refusal or an empty response is
        # billed like any other completion. Kept split by direction because
        # output tokens cost ~5x input, so one total can't be priced. Thinking
        # tokens are already inside `output_tokens`; the cache fields stay zero
        # until the pipeline starts caching.
        usage = response.usage
        result.input_tokens += usage.input_tokens
        result.output_tokens += usage.output_tokens
        result.cache_read_tokens += usage.cache_read_input_tokens or 0
        result.cache_write_tokens += usage.cache_creation_input_tokens or 0
        result.tokens_used += usage.input_tokens + usage.output_tokens

        if response.stop_reason == "refusal":
            raise PipelineError(key, f"{name} declined to analyze this content")

        output = _text_of(response)
        if not output:
            raise PipelineError(key, f"{name} returned an empty response")

        result.outputs[key] = output

        if on_agent_done:
            await on_agent_done(index, key, name, output)

    result.predictions = _parse_predictions(result.outputs["predictor"])
    return result
