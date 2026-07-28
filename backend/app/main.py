"""FastAPI application entrypoint."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRouter

from app.core.config import settings


def api_ok(data=None, msg: str = "ok") -> dict:
    """Unified success response envelope used across the API."""
    return {"code": 0, "msg": msg, "data": data}


app = FastAPI(title="AI Quant Platform", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def health() -> dict:
    return api_ok({"status": "up"})


app.include_router(router)
