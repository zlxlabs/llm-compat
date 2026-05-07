"""Tests for llm_compat.retry — smart retry with error classification."""
from __future__ import annotations

import httpx
import pytest

from llm_compat.errors import FatalError, RetryableError, TimeoutError, TruncationError
from llm_compat.retry import async_retry_call, sync_retry_call


class TestAsyncRetryCall:
    async def test_success_no_retry(self) -> None:
        call_count = 0

        async def fn() -> str:
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await async_retry_call(fn, max_retries=3)
        assert result == "ok"
        assert call_count == 1

    async def test_retries_on_retryable(self) -> None:
        call_count = 0

        async def fn() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.NetworkError("connection reset")
            return "ok"

        result = await async_retry_call(fn, max_retries=3, base_delay=0.01)
        assert result == "ok"
        assert call_count == 3

    async def test_fatal_error_no_retry(self) -> None:
        call_count = 0

        async def fn() -> str:
            nonlocal call_count
            call_count += 1
            raise httpx.HTTPStatusError(
                "401",
                request=httpx.Request("POST", "http://x"),
                response=httpx.Response(401),
            )

        with pytest.raises(FatalError):
            await async_retry_call(fn, max_retries=3, base_delay=0.01)
        assert call_count == 1

    async def test_timeout_no_retry(self) -> None:
        call_count = 0

        async def fn() -> str:
            nonlocal call_count
            call_count += 1
            raise httpx.ReadTimeout("timed out")

        with pytest.raises(TimeoutError):
            await async_retry_call(fn, max_retries=3, base_delay=0.01)
        assert call_count == 1

    async def test_truncation_no_retry(self) -> None:
        call_count = 0

        async def fn() -> str:
            nonlocal call_count
            call_count += 1
            raise Exception("unterminated string started at")

        with pytest.raises(TruncationError):
            await async_retry_call(fn, max_retries=3, base_delay=0.01)
        assert call_count == 1

    async def test_max_retries_exceeded(self) -> None:
        async def fn() -> str:
            raise httpx.NetworkError("fail")

        with pytest.raises(RetryableError):
            await async_retry_call(fn, max_retries=2, base_delay=0.01)

    async def test_total_timeout(self) -> None:
        call_count = 0

        async def fn() -> str:
            nonlocal call_count
            call_count += 1
            raise httpx.NetworkError("fail")

        with pytest.raises(RetryableError):
            await async_retry_call(fn, max_retries=100, base_delay=0.05, total_timeout=0.1)
        assert call_count < 100

    async def test_retry_after_header(self) -> None:
        call_count = 0

        async def fn() -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                resp = httpx.Response(429, headers={"Retry-After": "0.01"})
                raise httpx.HTTPStatusError(
                    "429", request=httpx.Request("POST", "http://x"), response=resp,
                )
            return "ok"

        result = await async_retry_call(fn, max_retries=3, base_delay=0.01)
        assert result == "ok"
        assert call_count == 2


class TestSyncRetryCall:
    def test_success(self) -> None:
        def fn() -> str:
            return "ok"

        assert sync_retry_call(fn, max_retries=3) == "ok"

    def test_retries_on_retryable(self) -> None:
        call_count = 0

        def fn() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.NetworkError("fail")
            return "ok"

        result = sync_retry_call(fn, max_retries=3, base_delay=0.01)
        assert result == "ok"

    def test_fatal_no_retry(self) -> None:
        def fn() -> str:
            raise httpx.HTTPStatusError(
                "401",
                request=httpx.Request("POST", "http://x"),
                response=httpx.Response(401),
            )

        with pytest.raises(FatalError):
            sync_retry_call(fn, max_retries=3, base_delay=0.01)
