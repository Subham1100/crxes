"""Every exception the backend raises, defined in one place.

The HTTP ones subclass `HTTPException`, so `raise AnalysisNotFound()` still
gets FastAPI's default JSON body and status code — the only change is that the
status/detail pair is named here instead of being spelled out at each raise
site, where the same wording tended to drift between routes.
"""

from fastapi import HTTPException, status


class ConfigError(RuntimeError):
    """A setting the server needs at runtime is missing or unusable."""


class MissingSecretError(ConfigError):
    """A required secret is unset. `remedy` tells the operator how to make one."""

    def __init__(self, name: str, remedy: str) -> None:
        super().__init__(f"{name} is empty — {remedy}")
        self.name = name


class PipelineError(RuntimeError):
    """An agent failed. `agent_key` identifies which one, for the UI."""

    #: Token usage from the agents that did complete before the failure, set by
    #: `run_pipeline` on its way out. A run that dies at the third agent has
    #: already spent money on the first two, and the analysis should say so.
    #: A plain dict rather than a `PipelineResult` — `agents` imports this
    #: module, so the type cannot be named here.
    usage: dict[str, int] | None = None

    def __init__(self, agent_key: str, message: str) -> None:
        super().__init__(message)
        self.agent_key = agent_key


class NotAuthenticated(HTTPException):
    """No session cookie or Bearer token, or one that no longer resolves."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )


class InvalidCredentials(HTTPException):
    """One message for every login failure mode — no account enumeration."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )


class EmailAlreadyRegistered(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists",
        )


class AnalysisNotFound(HTTPException):
    """Reads are scoped by user_id, so someone else's analysis is a 404, not a 403."""

    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis not found",
        )


class NoLogLines(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No log lines found in that input",
        )
