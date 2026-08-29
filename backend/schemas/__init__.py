"""Pydantic request/response models.

One module per resource, mirroring the router modules in `api/`. Routers import
from here rather than defining shapes inline, so the wire contract lives in one
place and can be reused by workers and clients.
"""

from schemas.agents import PredictedBug, PredictorOutput, Severity
from schemas.analyses import (
    AnalysisDetailOut,
    AnalysisOut,
    AnalyzeRequest,
    PredictionOut,
)
from schemas.auth import Credentials, LoginRequest, SignupRequest, UserOut
from schemas.health import HealthOut

__all__ = [
    "AnalysisDetailOut",
    "AnalysisOut",
    "AnalyzeRequest",
    "Credentials",
    "HealthOut",
    "LoginRequest",
    "PredictedBug",
    "PredictionOut",
    "PredictorOutput",
    "Severity",
    "SignupRequest",
    "UserOut",
]
