"""Health probe response model."""

from pydantic import BaseModel


class HealthOut(BaseModel):
    status: str
    #: "ok", or "error: {ExceptionClassName}" when the database is unreachable.
    database: str
