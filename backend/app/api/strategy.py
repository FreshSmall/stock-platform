"""Strategy list endpoint — reads from the strategy registry."""
from fastapi import APIRouter

from app.strategy import registry

router = APIRouter(prefix="/strategy", tags=["strategy"])


def _ok(data=None, msg: str = "ok") -> dict:
    # Lazy import to avoid the app.main <-> api.* circular import.
    from app.main import api_ok

    return api_ok(data, msg)


@router.get("")
def list_strategies():
    """Return all strategies with availability flags (V1 ma/macd available, V2 greyed out)."""
    items = []
    for meta in registry.all_strategies():
        items.append({
            "name": meta.name,
            "title": meta.title,
            "description": meta.description,
            "params": meta.params,
            "available": meta.available,
        })
    return _ok(items)
