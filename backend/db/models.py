from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, ForeignKey, Integer, LargeBinary, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[UUID]:
    return mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    # Null for accounts created through an OAuth provider — they never set one.
    password_hash: Mapped[str | None] = mapped_column(String(128))
    name: Mapped[str | None] = mapped_column(String(255))
    avatar_url: Mapped[str | None] = mapped_column(Text)
    plan: Mapped[str] = mapped_column(String(16), default="free", server_default="free")
    created_at: Mapped[datetime] = mapped_column(default=func.now(), server_default=func.now())

    sources: Mapped[list["Source"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    analyses: Mapped[list["Analysis"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[UUID] = _pk()
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # "datadog" | "cloudwatch" | "gcp" | "sentry" | "loki" | "webhook" | "manual"
    provider: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(255))
    config: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    credentials_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)
    # Only set for provider="webhook" — the URL path segment doubles as the auth token.
    webhook_token: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    # "manual" | "15min" | "30min" | "1hr" | "4hr"
    schedule: Mapped[str] = mapped_column(String(16), default="manual", server_default="manual")
    auto_analyze: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    last_pulled_at: Mapped[datetime | None]
    # "active" | "paused" | "error"
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=func.now(), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="sources")
    log_pulls: Mapped[list["LogPull"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class LogPull(Base):
    __tablename__ = "log_pulls"

    id: Mapped[UUID] = _pk()
    source_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), index=True
    )
    log_count: Mapped[int] = mapped_column(Integer, default=0)
    time_range_start: Mapped[datetime | None]
    time_range_end: Mapped[datetime | None]
    # array of NormalizedLogEntry dicts
    normalized_logs: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    raw_size_bytes: Mapped[int | None] = mapped_column(Integer)
    pulled_at: Mapped[datetime] = mapped_column(default=func.now(), server_default=func.now())

    source: Mapped["Source"] = relationship(back_populates="log_pulls")


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[UUID] = _pk()
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("sources.id", ondelete="SET NULL")
    )
    log_pull_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("log_pulls.id", ondelete="SET NULL")
    )
    # "pending" | "running" | "done" | "failed"
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")

    agent_parser_output: Mapped[str | None] = mapped_column(Text)
    agent_pattern_output: Mapped[str | None] = mapped_column(Text)
    agent_rootcause_output: Mapped[str | None] = mapped_column(Text)
    agent_predictor_output: Mapped[str | None] = mapped_column(Text)
    current_agent: Mapped[int | None] = mapped_column(Integer, default=0, server_default="0")

    log_line_count: Mapped[int | None] = mapped_column(Integer)
    total_tokens_used: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(default=func.now(), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="analyses")
    predictions: Mapped[list["Prediction"]] = relationship(
        back_populates="analysis", cascade="all, delete-orphan"
    )


class Prediction(Base):
    __tablename__ = "predictions"

    id: Mapped[UUID] = _pk()
    analysis_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("analyses.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(Text)
    # "critical" | "high" | "medium" | "low"
    severity: Mapped[str] = mapped_column(String(16), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[int | None] = mapped_column(Integer)  # 0-100
    eta: Mapped[str | None] = mapped_column(String(64))
    impact: Mapped[str | None] = mapped_column(Text)
    root_cause: Mapped[str | None] = mapped_column(Text)
    recommended_action: Mapped[str | None] = mapped_column(Text)
    was_accurate: Mapped[bool | None] = mapped_column(Boolean)
    feedback_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=func.now(), server_default=func.now())

    analysis: Mapped["Analysis"] = relationship(back_populates="predictions")
