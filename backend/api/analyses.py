"""Analyses — run the agent pipeline against pasted logs, then read results back.

Phase 1 runs the pipeline inline: `POST /api/analyses` blocks until all four
agents finish. Phase 3 keeps these routes and moves the run onto Celery, with
`GET /api/analyses/{id}/stream` added alongside.
"""

import time
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agents import AGENTS, run_pipeline
from api.deps import get_current_user
from config import settings
from core import cost, logs, tokens
from db.models import Analysis, LogPull, Prediction, Source, User
from db.session import get_db
from exceptions import AnalysisNotFound, NoLogLines, PipelineError
from schemas.analyses import AnalysisDetailOut, AnalysisOut, AnalyzeRequest
from schemas.costs import CostEstimateOut, EstimateRequest

router = APIRouter(prefix="/api/analyses", tags=["analyses"])

#: Pasted logs still get a Source row so the schema stays uniform — one manual
#: source per user, reused across every paste.
MANUAL_SOURCE_NAME = "Pasted logs"


async def _manual_source(db: AsyncSession, user: User) -> Source:
    source = await db.scalar(
        select(Source).where(Source.user_id == user.id, Source.provider == "manual")
    )
    if source is None:
        source = Source(
            user_id=user.id,
            provider="manual",
            name=MANUAL_SOURCE_NAME,
            schedule="manual",
            auto_analyze=False,
        )
        db.add(source)
        await db.flush()
    return source


def _record_cost(analysis: Analysis, usage: dict[str, int]) -> None:
    """Write reported token usage, and what it cost, onto the analysis row.

    `cost_usd` stays null for a model missing from the pricing catalog — a
    missing price is worth showing as "unpriced", not as $0.00.
    """
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)

    analysis.model = settings.anthropic_model
    analysis.input_tokens = input_tokens
    analysis.output_tokens = output_tokens
    analysis.total_tokens_used = input_tokens + output_tokens

    usd = cost.actual_cost_usd(
        settings.anthropic_model,
        input_tokens,
        output_tokens,
        usage.get("cache_read_tokens", 0),
        usage.get("cache_write_tokens", 0),
    )
    analysis.cost_usd = Decimal(str(round(usd, 6))) if usd is not None else None


async def _load(db: AsyncSession, user: User, analysis_id: UUID) -> Analysis:
    analysis = await db.scalar(
        select(Analysis)
        .where(Analysis.id == analysis_id, Analysis.user_id == user.id)
        .options(selectinload(Analysis.predictions))
    )
    if analysis is None:
        raise AnalysisNotFound()
    return analysis


@router.post("/estimate", response_model=CostEstimateOut)
async def estimate_cost(
    body: EstimateRequest,
    user: Annotated[User, Depends(get_current_user)],
) -> CostEstimateOut:
    """Price a paste before running it.

    Parses through the same `logs.normalize` / `logs.to_prompt` path the real
    run uses, so the estimate is built from the exact text the parser would be
    sent — truncation of an oversized paste included.
    """
    entries, dropped = logs.normalize(body.logs)
    if not entries:
        raise NoLogLines()

    prompt = logs.to_prompt(entries)
    log_tokens, counted = await tokens.count_tokens(prompt)
    estimate = cost.estimate_pipeline_tokens(log_tokens, counted=counted)

    return CostEstimateOut.of(
        estimate,
        cost.price_catalog(estimate),
        log_line_count=len(entries),
        dropped_lines=dropped,
        raw_size_bytes=len(body.logs.encode()),
        prompt_chars=len(prompt),
    )


@router.post("", response_model=AnalysisDetailOut, status_code=status.HTTP_201_CREATED)
async def create_analysis(
    body: AnalyzeRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> AnalysisDetailOut:
    entries, dropped = logs.normalize(body.logs)
    if not entries:
        raise NoLogLines()

    source = await _manual_source(db, user)
    log_pull = LogPull(
        source_id=source.id,
        log_count=len(entries),
        normalized_logs=entries,
        raw_size_bytes=len(body.logs.encode()),
    )
    db.add(log_pull)
    await db.flush()

    analysis = Analysis(
        user_id=user.id,
        source_id=source.id,
        log_pull_id=log_pull.id,
        status="running",
        current_agent=0,
        log_line_count=len(entries),
    )
    db.add(analysis)
    await db.commit()
    await db.refresh(analysis)

    # Column names are `agent_{key}_output`, keyed off the pipeline's agent keys.
    async def checkpoint(index: int, key: str, name: str, output: str) -> None:
        setattr(analysis, f"agent_{key}_output", output)
        analysis.current_agent = index + 1
        await db.commit()

    started = time.monotonic()
    try:
        result = await run_pipeline(logs.to_prompt(entries), on_agent_done=checkpoint)
    except PipelineError as exc:
        analysis.status = "failed"
        analysis.error_message = str(exc)
        # A run that died at the third agent still paid for the first two.
        _record_cost(analysis, exc.usage or {})
        analysis.duration_ms = int((time.monotonic() - started) * 1000)
        await db.commit()
        # 200 with status="failed" — the partial agent output is worth showing,
        # and the client renders the error from the row.
        return AnalysisDetailOut.of(await _load(db, user, analysis.id))

    for row in result.predictions:
        db.add(Prediction(analysis_id=analysis.id, **row))

    analysis.status = "done"
    analysis.current_agent = len(AGENTS)
    _record_cost(analysis, result.usage())
    analysis.duration_ms = int((time.monotonic() - started) * 1000)
    if dropped:
        analysis.error_message = f"{dropped:,} lines past the {logs.MAX_LINES:,}-line cap were dropped"
    await db.commit()

    return AnalysisDetailOut.of(await _load(db, user, analysis.id))


@router.get("", response_model=list[AnalysisOut])
async def list_analyses(
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[AnalysisOut]:
    rows = await db.scalars(
        select(Analysis)
        .where(Analysis.user_id == user.id)
        .order_by(desc(Analysis.created_at))
        .limit(limit)
    )
    return [AnalysisOut.of(a) for a in rows]


@router.get("/{analysis_id}", response_model=AnalysisDetailOut)
async def get_analysis(
    analysis_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> AnalysisDetailOut:
    return AnalysisDetailOut.of(await _load(db, user, analysis_id))


@router.get("/{analysis_id}/logs", response_model=list[dict[str, Any]])
async def get_analysis_logs(
    analysis_id: UUID,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """The normalized entries the pipeline actually saw."""
    analysis = await _load(db, user, analysis_id)
    if analysis.log_pull_id is None:
        return []
    pull = await db.get(LogPull, analysis.log_pull_id)
    return pull.normalized_logs if pull else []
