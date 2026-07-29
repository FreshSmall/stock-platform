"""Process-internal cache (V1: no Redis).

Provides a module-level shared :class:`cachetools.TTLCache` plus a
:func:`cached` decorator for memoising function results with a per-key TTL.

This is intentionally a single-process, in-memory cache. Once V2 introduces
Redis, the public surface (``get_cache`` / ``cached``) can stay the same while
the backing implementation swaps over.
"""

from collections.abc import Callable
from functools import wraps

from cachetools import TTLCache

_default_cache: TTLCache = TTLCache(maxsize=1024, ttl=300)


def get_cache() -> TTLCache:
    """Return the shared process-wide cache instance."""
    return _default_cache


def cached(ttl: int = 300, key_builder: Callable | None = None):
    """Decorator: cache a function's return value in a per-process TTLCache.

    Each decorated function gets its own :class:`TTLCache` so caches don't
    evict one another. Keys are built from the call args.

    Args:
        ttl: time-to-live in seconds.
        key_builder: optional callable ``(args, kwargs) -> hashable``.
            Defaults to ``(args, tuple(sorted(kwargs.items())))``.
    """

    def decorator(func):
        store: TTLCache = TTLCache(maxsize=1024, ttl=ttl)

        @wraps(func)
        def wrapper(*args, **kwargs):
            if key_builder is not None:
                key = key_builder(args, kwargs)
            else:
                key = (args, tuple(sorted(kwargs.items())))
            if key in store:
                return store[key]
            result = func(*args, **kwargs)
            store[key] = result
            return result

        return wrapper

    return decorator
