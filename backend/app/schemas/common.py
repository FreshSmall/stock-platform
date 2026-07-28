"""Shared schemas used by every API module: the unified response envelope
and pagination helpers.

The envelope is::

    {"code": 0, "msg": "ok", "data": <T>}

``code == 0`` means success; non-zero codes are business errors (see
:mod:`app.core.errors`). HTTP status remains 200 even for business errors,
following the common Chinese-API convention.
"""

from typing import Generic, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """Unified API response envelope wrapping an arbitrary payload ``T``."""

    code: int = 0
    msg: str = "ok"
    data: Optional[T] = None


class PageParams(BaseModel):
    """1-based pagination request parameters.

    ``offset`` is derived from ``page`` and ``size`` so callers never need to
    compute it by hand when building SQL ``LIMIT``/``OFFSET`` clauses.
    """

    page: int = 1  # 1-based
    size: int = 20

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


class PageResult(BaseModel, Generic[T]):
    """Paginated response payload carrying the page slice plus totals."""

    total: int
    page: int
    size: int
    items: list[T]
