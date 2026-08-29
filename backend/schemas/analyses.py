"""Analysis request/response models."""

from pydantic import BaseModel, Field

from core import logs
from db.models import Analysis, Prediction


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
    input_tokens: int | None
    output_tokens: int | None
    model: str | None
    #: What the run actually cost, in USD, from the reported token usage.
    cost_usd: float | None
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
            input_tokens=a.input_tokens,
            output_tokens=a.output_tokens,
            model=a.model,
            cost_usd=float(a.cost_usd) if a.cost_usd is not None else None,
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
