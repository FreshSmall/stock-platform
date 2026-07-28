"""Unified business error and global exception handlers.

A :class:`BizError` carries a business ``code`` (non-zero) and ``msg``. The
registered FastAPI handler renders it into the unified envelope while keeping
HTTP status 200, per the common Chinese-API convention (``code`` in the body
signals success/failure, not the HTTP status code).
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class BizError(Exception):
    """Business error rendered into the unified response envelope."""

    def __init__(self, code: int, msg: str):
        self.code = code
        self.msg = msg
        super().__init__(msg)


def register_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers on ``app``."""

    @app.exception_handler(BizError)
    async def _biz_error_handler(_request: Request, exc: BizError):
        return JSONResponse(
            status_code=200,  # business error, HTTP still 200
            content={"code": exc.code, "msg": exc.msg, "data": None},
        )
