"""Tests for shared infrastructure introduced in Task A4:

- :mod:`app.schemas.common` (response envelope + pagination)
- :mod:`app.core.cache` (TTLCache + ``cached`` decorator)
- :mod:`app.core.errors` (``BizError`` + handlers)
- :mod:`app.core.deps` (``get_db`` session lifecycle)
"""

import pytest
from sqlalchemy.orm import Session

from app.core.cache import cached, get_cache
from app.core.database import SessionLocal
from app.core.deps import get_db
from app.core.errors import BizError
from app.schemas.common import ApiResponse, PageParams, PageResult


# --- schemas -----------------------------------------------------------------


def test_api_response_envelope() -> None:
    """ApiResponse wraps a dict payload with default code/msg."""
    resp = ApiResponse(data={"x": 1})
    assert resp.model_dump() == {"code": 0, "msg": "ok", "data": {"x": 1}}


def test_api_response_defaults_to_empty_data() -> None:
    """Without data, the envelope is still well-formed (data is None)."""
    resp = ApiResponse()
    assert resp.model_dump() == {"code": 0, "msg": "ok", "data": None}


def test_page_result() -> None:
    """PageResult exposes total/page/size/items verbatim."""
    result = PageResult(total=100, page=1, size=20, items=[1, 2, 3])
    assert result.total == 100
    assert result.page == 1
    assert result.size == 20
    assert result.items == [1, 2, 3]


def test_page_params_offset() -> None:
    """offset is (page - 1) * size, e.g. page=3 size=10 -> 20."""
    params = PageParams(page=3, size=10)
    assert params.offset == 20


def test_page_params_default_offset_is_zero() -> None:
    """First page yields a zero offset."""
    assert PageParams().offset == 0


# --- cache -------------------------------------------------------------------


def test_get_cache_returns_shared_instance() -> None:
    """get_cache() returns the module-level TTLCache."""
    from app.core import cache as cache_module

    assert get_cache() is cache_module._default_cache


def test_cache_decorator_caches() -> None:
    """A cached function runs once per distinct arg tuple."""

    calls = {"n": 0}

    @cached(ttl=60)
    def slow_add(a: int, b: int) -> int:
        calls["n"] += 1
        return a + b

    # Same args -> computed once, then served from the cache.
    assert slow_add(1, 2) == 3
    assert slow_add(1, 2) == 3
    assert calls["n"] == 1

    # Different args -> computed again.
    assert slow_add(3, 4) == 7
    assert calls["n"] == 2

    # The first key is still cached, so no extra computation.
    assert slow_add(1, 2) == 3
    assert calls["n"] == 2


def test_cache_decorator_distinguishes_kwargs() -> None:
    """Different kwargs produce different cache keys."""
    calls = {"n": 0}

    @cached(ttl=60)
    def fn(x: int, *, mode: str = "a") -> str:
        calls["n"] += 1
        return f"{x}-{mode}"

    assert fn(1) == "1-a"
    assert fn(1, mode="b") == "1-b"
    assert fn(1) == "1-a"  # served from cache
    assert calls["n"] == 2


# --- errors ------------------------------------------------------------------


def test_biz_error_raises() -> None:
    """BizError carries its code/msg and can be raised/caught."""
    with pytest.raises(BizError):
        raise BizError(1001, "bad")


def test_biz_error_carries_code_and_msg() -> None:
    """Code and message are readable off the exception instance."""
    err = BizError(1001, "bad")
    assert err.code == 1001
    assert err.msg == "bad"
    assert str(err) == "bad"


# --- deps --------------------------------------------------------------------


def test_get_db_yields_session(monkeypatch) -> None:
    """get_db() yields a live Session and closes it when the generator ends.

    Note: in SQLAlchemy 2.0 ``Session.close()`` does not flip ``is_active`` to
    False, so we record the actual ``close()`` call with a spy instead.
    """
    gen = get_db()
    session = next(gen)
    closed = {"yes": False}

    def _spy_close(self):
        closed["yes"] = True
        return _orig_close(self)

    _orig_close = Session.close
    try:
        assert isinstance(session, Session)
        assert session.is_active  # open before the generator is exhausted

        # Hand control back to the generator so its ``finally`` runs.
        monkeypatch.setattr(Session, "close", _spy_close)
        with pytest.raises(StopIteration):
            next(gen)
    finally:
        monkeypatch.setattr(Session, "close", _orig_close)

    assert closed["yes"], "get_db() must close the session in its finally block"
