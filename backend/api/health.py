from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_db

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("/")
async def health(db: AsyncSession = Depends(get_db)) -> dict:
    """Uptime probe. Reports database reachability without failing the request."""
    try:
        await db.execute(text("SELECT 1"))
        database = "ok"
    except Exception as exc:  # noqa: BLE001 — probe must always answer
        database = f"error: {exc.__class__.__name__}"

    return {"status": "ok", "database": database}
