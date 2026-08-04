"""Analyses — run the agent pipeline against pasted logs, then read results back.

Phase 1 runs the pipeline inline: `POST /api/analyses` blocks until all four
agents finish. Phase 3 keeps these routes and moves the run onto Celery, with
`GET /api/analyses/{id}/stream` added alongside.
"""

import time
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agents import AGENTS, PipelineError, run_pipeline
from api.deps import get_current_user
from core import logs
from db.models import Analysis, LogPull, Prediction, Source, User
from db.session import get_db

router = APIRouter(prefix="/api/analyses", tags=["analyses"])

#: Pasted logs still get a Source row so the schema stays uniform — one manual
#: source per user, reused across every paste.
MANUAL_SOURCE_NAME = "Pasted logs"


class AnalyzeRequest(BaseModel):
    logs: str = Field(min_length=1, max_length=logs.MAX_BYTES)


class PredictionOut(BaseModel):
    id: str
    analysis_id: str
    title: str
    severity: str
    description: str | None
    confidence: int | None
    eta: str | None
    impact: str | None
    root_cause: str | None
    recommended_action: str | None
    was_accurate: bool | None
    feedback_note: str | None
    created_at: str

    @classmethod
    def of(cls, p: Prediction) -> "PredictionOut":
        return cls(
            id=str(p.id),
            analysis_id=str(p.analysis_id),
            title=p.title,
            severity=p.severity,
            description=p.description,
            confidence=p.confidence,
            eta=p.eta,
            impact=p.impact,
            root_cause=p.root_cause,
            recommended_action=p.recommended_action,
            was_accurate=p.was_accurate,
            feedback_note=p.feedback_note,
            created_at=p.created_at.isoformat(),
        )


class AnalysisOut(BaseModel):
    id: str
    source_id: str | None
    log_pull_id: str | None
    status: str
    current_agent: int | None
    log_line_count: int | None
    total_tokens_used: int | None
    duration_ms: int | None
    error_message: str | None
    created_at: str

    @classmethod
    def of(cls, a: Analysis) -> "AnalysisOut":
        return cls(
            id=str(a.id),
            source_id=str(a.source_id) if a.source_id else None,
            log_pull_id=str(a.log_pull_id) if a.log_pull_id else None,
            status=a.status,
            current_agent=a.current_agent,
            log_line_count=a.log_line_count,
            total_tokens_used=a.total_tokens_used,
            duration_ms=a.duration_ms,
            error_message=a.error_message,
            created_at=a.created_at.isoformat(),
        )


class AnalysisDetailOut(AnalysisOut):
    agent_parser_output: str | None
    agent_pattern_output: str | None
    agent_rootcause_output: str | None
    agent_predictor_output: str | None
    predictions: list[PredictionOut]

    @classmethod
    def of(cls, a: Analysis) -> "AnalysisDetailOut":
        return cls(
            **AnalysisOut.of(a).model_dump(),
            agent_parser_output=a.agent_parser_output,
            agent_pattern_output=a.agent_pattern_output,
            agent_rootcause_output=a.agent_rootcause_output,
            agent_predictor_output=a.agent_predictor_output,
            predictions=[PredictionOut.of(p) for p in a.predictions],
        )


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


async def _load(db: AsyncSession, user: User, analysis_id: UUID) -> Analysis:
    analysis = await db.scalar(
        select(Analysis)
        .where(Analysis.id == analysis_id, Analysis.user_id == user.id)
        .options(selectinload(Analysis.predictions))
    )
    if analysis is None:
        # Scoped by user_id, so someone else's analysis is a 404, not a 403.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis not found")
    return analysis


@router.post("", response_model=AnalysisDetailOut, status_code=status.HTTP_201_CREATED)
async def create_analysis(
    body: AnalyzeRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db),
) -> AnalysisDetailOut:
    entries, dropped = logs.normalize(body.logs)
    if not entries:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No log lines found in that input",
        )

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
        analysis.duration_ms = int((time.monotonic() - started) * 1000)
        await db.commit()
        # 200 with status="failed" — the partial agent output is worth showing,
        # and the client renders the error from the row.
        return AnalysisDetailOut.of(await _load(db, user, analysis.id))

    for row in result.predictions:
        db.add(Prediction(analysis_id=analysis.id, **row))

    analysis.status = "done"
    analysis.current_agent = len(AGENTS)
    analysis.total_tokens_used = result.tokens_used
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
