"""The agent pipeline's contract with Claude, and its handoff to the API layer.

`PredictorOutput` is both halves of the Bug Predictor's structured output: the
JSON schema sent on the request (via `anthropic.transform_schema`) and the
validator for what comes back. Keeping one definition means the schema can't
drift from the parsing.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Mirrors the `severity` values the Prediction column documents.
Severity = Literal["critical", "high", "medium", "low"]

#: `predictions.eta` is String(64) in db/models.py.
ETA_MAX_CHARS = 64


class PredictedBug(BaseModel):
    """One predicted failure. Field names match the Prediction columns."""

    # Structured outputs require `additionalProperties: false`, which pydantic
    # only emits under extra="forbid".
    model_config = ConfigDict(extra="forbid")

    title: str
    severity: Severity
    description: str
    # Numeric bounds are stripped out of the schema by the API, so they'd be
    # unenforced on the way in and would reject a valid response on the way
    # out — the range is stated for the model and clamped in `to_row`.
    confidence: int = Field(description="Likelihood this happens, 0-100.")
    eta: str = Field(description="Short phrase, e.g. 'within 2 hours'.")
    impact: str
    root_cause: str
    recommended_action: str

    @field_validator("severity", mode="before")
    @classmethod
    def known_severity(cls, v: Any) -> Any:
        """The enum is server-enforced; this is the belt to the API's braces."""
        v = str(v).lower().strip()
        return v if v in ("critical", "high", "medium", "low") else "medium"

    def to_row(self) -> dict[str, Any]:
        """Kwargs for a `db.models.Prediction`, with the column limits applied."""
        return {
            "title": self.title.strip(),
            "severity": self.severity,
            "description": self.description or None,
            "confidence": max(0, min(100, self.confidence)),
            "eta": self.eta.strip()[:ETA_MAX_CHARS] or None,
            "impact": self.impact or None,
            "root_cause": self.root_cause or None,
            "recommended_action": self.recommended_action or None,
        }


class PredictorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    predictions: list[PredictedBug]

    def rows(self, limit: int) -> list[dict[str, Any]]:
        """The predictions worth storing, capped at `limit`.

        A bug with no title has nothing to render, so it is dropped rather than
        counted against the cap.
        """
        titled = (bug for bug in self.predictions if bug.title.strip())
        return [bug.to_row() for bug in list(titled)[:limit]]
