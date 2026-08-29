"""Shared FastAPI dependencies."""

from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.security import decode_session_token
from db.models import User
from db.session import get_db
from exceptions import NotAuthenticated


def _read_token(request: Request) -> str | None:
    """Session cookie first; Bearer header as a fallback for API clients."""
    cookie = request.cookies.get(settings.session_cookie_name)
    if cookie:
        return cookie

    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    token = _read_token(request)
    if not token:
        raise NotAuthenticated()

    claims = decode_session_token(token)
    if not claims:
        raise NotAuthenticated()

    try:
        user_id = UUID(claims["sub"])
    except (KeyError, ValueError):
        raise NotAuthenticated() from None

    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        # Valid signature, but the account is gone — treat as logged out.
        raise NotAuthenticated()
    return user
