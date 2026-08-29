"""Auth request/response models."""

from pydantic import BaseModel, EmailStr, Field, field_validator

from core.security import MAX_PASSWORD_BYTES
from db.models import User


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("password")
    @classmethod
    def fits_bcrypt(cls, v: str) -> str:
        if len(v.encode()) > MAX_PASSWORD_BYTES:
            raise ValueError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes")
        return v


class SignupRequest(Credentials):
    name: str | None = Field(default=None, max_length=255)

    @field_validator("name")
    @classmethod
    def blank_is_none(cls, v: str | None) -> str | None:
        return v.strip() or None if v else None


class LoginRequest(Credentials):
    pass


class UserOut(BaseModel):
    id: str
    email: str
    name: str | None
    avatar_url: str | None
    plan: str
    created_at: str

    @classmethod
    def of(cls, user: User) -> "UserOut":
        return cls(
            id=str(user.id),
            email=user.email,
            name=user.name,
            avatar_url=user.avatar_url,
            plan=user.plan,
            created_at=user.created_at.isoformat(),
        )
