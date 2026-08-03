from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import health
from config import settings

app = FastAPI(title="crxes.app API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)


@app.get("/")
async def root() -> dict:
    return {"service": "crxes.app API", "docs": "/docs"}
