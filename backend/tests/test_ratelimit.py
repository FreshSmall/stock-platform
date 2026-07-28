"""Tests for F2: token-bucket limiter + the assistant/analysis quota helpers."""

import time

import pytest
from fastapi import HTTPException

from app.core.ratelimit import (
    TokenBucketLimiter,
    _analysis_limiter,
    _assistant_limiter,
    check_analysis_quota,
    check_assistant_quota,
)


def test_token_bucket_allows_until_exhausted():
    limiter = TokenBucketLimiter(capacity=3, refill_per_sec=0, key_func=None)
    assert limiter.check("k") is True
    assert limiter.check("k") is True
    assert limiter.check("k") is True
    assert limiter.check("k") is False


def test_token_bucket_refills_over_time():
    limiter = TokenBucketLimiter(capacity=1, refill_per_sec=100, key_func=None)
    assert limiter.check("k") is True
    assert limiter.check("k") is False
    time.sleep(0.05)  # 0.05s * 100 = 5 tokens refilled
    assert limiter.check("k") is True


def test_token_bucket_keys_are_independent():
    limiter = TokenBucketLimiter(capacity=1, refill_per_sec=0, key_func=None)
    assert limiter.check("a") is True
    assert limiter.check("a") is False
    # a different key has its own bucket
    assert limiter.check("b") is True


def test_check_assistant_quota_raises_when_exhausted():
    _assistant_limiter._buckets.clear()
    # exhaust the capacity (10) for a test user
    for _ in range(10):
        check_assistant_quota(999888)
    with pytest.raises(HTTPException) as exc:
        check_assistant_quota(999888)
    assert exc.value.status_code == 429
    _assistant_limiter._buckets.clear()


def test_check_assistant_quota_anon_key():
    """No user_id falls back to the 'anon' bucket without error."""
    _assistant_limiter._buckets.clear()
    check_assistant_quota(None)  # should not raise
    _assistant_limiter._buckets.clear()


def test_check_analysis_quota_allows_initially():
    _analysis_limiter._buckets.clear()
    check_analysis_quota(777666)  # should not raise
    _analysis_limiter._buckets.clear()


def test_check_analysis_quota_raises_when_exhausted():
    _analysis_limiter._buckets.clear()
    for _ in range(6):
        check_analysis_quota(555444)
    with pytest.raises(HTTPException) as exc:
        check_analysis_quota(555444)
    assert exc.value.status_code == 429
    _analysis_limiter._buckets.clear()
