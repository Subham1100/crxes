"""Password hashing and session-token signing.

Tokens are signed with NEXTAUTH_SECRET so the Next.js side can verify the same
session once NextAuth lands in Phase 2 (see frontend/.env.example).
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import bcrypt
from jose import JWTError, jwt

from config import settings

ALGORITHM = "HS256"

#: bcrypt silently truncates anything past 72 bytes — reject instead, so a long
#: passphrase can't be logged in with only its first 72 bytes.
MAX_PASSWORD_BYTES = 72


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        # Malformed hash in the row (hand-edited, or written by another tool).
        return False


def _secret() -> str:
    if not settings.nextauth_secret:
        raise RuntimeError(
            "NEXTAUTH_SECRET is empty — generate one with `openssl rand -base64 32` "
            "and set it in backend/.env and frontend/.env.local (identical values)."
        )
    return settings.nextauth_secret


def create_session_token(user_id: UUID, email: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "email": email,
        "iat": now,
        "exp": now + timedelta(hours=settings.session_ttl_hours),
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


def decode_session_token(token: str) -> dict | None:
    """Return the claims, or None if the token is expired, forged, or garbage."""
    try:
        return jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except JWTError:
        return None
