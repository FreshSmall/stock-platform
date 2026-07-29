"""Token-bucket rate limiting (process-local; Redis-backed in V2).

This module provides a small, dependency-free token-bucket implementation and
two pre-built limiters covering the per-USER quotas used by the AI endpoints:

* ``check_assistant_quota`` - assistant messages (10 / user / minute).
* ``check_analysis_quota``  - analysis triggers (6 / user / minute).

The per-STOCK analysis cooldown lives in ``analysis_service._rate_limit`` and is
applied on top of the per-user quota; the two are independent concerns.

State is held in-process (``defaultdict`` of buckets), which is correct for the
single-worker MVP deployment. Under multiple workers each worker gets its own
bucket map and the effective limit is ``limit * workers``; a future task will
move this to Redis for exact cross-process accounting.
"""

import time
from collections import defaultdict
from typing import Callable, Optional

from fastapi import HTTPException, status


class _Bucket:
    """One token bucket. ``allow`` consumes a token or returns False."""

    __slots__ = ("capacity", "refill_per_sec", "tokens", "last")

    def __init__(self, capacity: float, refill_per_sec: float):
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self.tokens = capacity
        self.last = time.monotonic()

    def allow(self) -> bool:
        """Refill based on elapsed wall-clock, then consume one token if possible."""
        now = time.monotonic()
        self.tokens = min(
            self.capacity, self.tokens + (now - self.last) * self.refill_per_sec
        )
        self.last = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class TokenBucketLimiter:
    """Per-key token-bucket limiter.

    ``key_func`` is kept for API symmetry with a future Redis-backed generic
    dependency; the pre-built helpers below build keys directly.
    """

    def __init__(
        self,
        capacity: float,
        refill_per_sec: float,
        key_func: Optional[Callable] = None,
    ):
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self.key_func = key_func
        self._buckets: dict = defaultdict(lambda: _Bucket(capacity, refill_per_sec))

    def check(self, key) -> bool:
        """Return True if a request for ``key`` is allowed, False if exhausted."""
        return self._buckets[key].allow()


# --- Pre-built limiters -----------------------------------------------------
# Assistant: 10 messages per user per minute -> capacity 10, refill 10/60 per sec.
_assistant_limiter = TokenBucketLimiter(
    capacity=10,
    refill_per_sec=10 / 60,
    key_func=lambda req, **kw: kw.get("user_id", "anon"),
)
# Analysis trigger: 6 requests per user per minute (the per-STOCK cooldown in
# analysis_service is a separate, additional limit).
_analysis_limiter = TokenBucketLimiter(
    capacity=6,
    refill_per_sec=6 / 60,
    key_func=lambda req, **kw: kw.get("user_id", "anon"),
)


def _key(scope: str, user_id: int | None) -> tuple[str, int | str]:
    return (scope, user_id if user_id is not None else "anon")


def check_assistant_quota(user_id: int | None) -> None:
    """Raise HTTP 429 if the assistant quota for ``user_id`` is exhausted."""
    if not _assistant_limiter.check(_key("assistant", user_id)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="请求过于频繁，请稍后再试",
        )


def check_analysis_quota(user_id: int | None) -> None:
    """Raise HTTP 429 if the analysis quota for ``user_id`` is exhausted."""
    if not _analysis_limiter.check(_key("analysis", user_id)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="分析请求过于频繁，请稍后再试",
        )
