"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRouter

from app.api.analysis import router as analysis_router
from app.api.assistant import router as assistant_router
from app.api.auth import router as auth_router
from app.api.backtest import router as backtest_router
from app.api.dragon_tiger import router as dragon_tiger_router
from app.api.market import router as market_router
from app.api.sector import router as sector_router
from app.api.stock import router as stock_router
from app.api.strategy import router as strategy_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.scheduler import init_scheduler, shutdown_scheduler


def api_ok(data=None, msg: str = "ok") -> dict:
    """Unified success response envelope used across the API."""
    return {"code": 0, "msg": msg, "data": data}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the APScheduler on startup, shut it down on exit.

    The scheduler is created lazily inside :func:`init_scheduler`, so importing
    this module (e.g. in tests) has no background-thread side effects.
    """
    init_scheduler()
    try:
        yield
    finally:
        shutdown_scheduler()


app = FastAPI(title="AI Quant Platform", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Render BizError into the unified envelope while keeping HTTP 200.
register_exception_handlers(app)

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def health() -> dict:
    return api_ok({"status": "up"})


router.include_router(stock_router)
router.include_router(market_router)
router.include_router(auth_router)
router.include_router(backtest_router)
router.include_router(strategy_router)
router.include_router(analysis_router)
router.include_router(assistant_router)
router.include_router(dragon_tiger_router)
router.include_router(sector_router)
app.include_router(router)
