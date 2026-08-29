"""Email + password auth.

OAuth (GitHub, Google) lands in Phase 2 and will create rows in the same
`users` table with `password_hash` left null.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user
from config import settings
from core.security import create_session_token, hash_password, verify_password
from db.models import User
from db.session import get_db
from exceptions import EmailAlreadyRegistered, InvalidCredentials
from schemas.auth import LoginRequest, SignupRequest, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])

#: Compared against when an email doesn't exist, so a miss costs the same as a
#: wrong password and can't be distinguished by response time.
_DUMMY_HASH = "$2b$12$Bgy9xZEpCEJji.cthqbameyp72X//uKYRGnweAzV8nLQZ2VdXILae"


def _set_session_cookie(response: Response, user: User) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=create_session_token(user.id, user.email),
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        domain=settings.session_cookie_domain,
        path="/",
    )


@router.post("/signup", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignupRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        name=body.name,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        # Unique index on users.email — the only constraint this insert can trip.
        await db.rollback()
        raise EmailAlreadyRegistered() from None

    await db.refresh(user)
    _set_session_cookie(response, user)
    return UserOut.of(user)


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    user = await db.scalar(select(User).where(User.email == body.email))

    if user is None or user.password_hash is None:
        verify_password(body.password, _DUMMY_HASH)
        raise InvalidCredentials()
    if not verify_password(body.password, user.password_hash):
        raise InvalidCredentials()

    _set_session_cookie(response, user)
    return UserOut.of(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    response.delete_cookie(
        key=settings.session_cookie_name,
        domain=settings.session_cookie_domain,
        path="/",
    )


@router.get("/me", response_model=UserOut)
async def me(user: Annotated[User, Depends(get_current_user)]) -> UserOut:
    return UserOut.of(user)
