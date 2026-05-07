from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from .errors import (
    FatalError,
    RetryableError,
    TimeoutError,
    TruncationError,
    classify_error,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

_NO_RETRY_TYPES = (TimeoutError, TruncationError)


def _get_retry_after(exc: Exception) -> float | None:
    if isinstance(exc, httpx.HTTPStatusError):
        val = exc.response.headers.get("Retry-After")
        if val:
            try:
                return float(val)
            except ValueError:
                pass
    return None


def _compute_backoff(attempt: int, base_delay: float, max_delay: float) -> float:
    delay = min(base_delay * (2**attempt), max_delay)
    return delay * (0.5 + random.random())


async def async_retry_call(
    fn: Callable[..., Awaitable[T]],
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    total_timeout: float = 300.0,
) -> T:
    start = time.monotonic()

    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except Exception as exc:
            error_cls = classify_error(exc)

            if error_cls == FatalError:
                raise FatalError(str(exc)) from exc

            if issubclass(error_cls, _NO_RETRY_TYPES):
                raise error_cls(str(exc)) from exc

            if attempt >= max_retries:
                raise RetryableError(f"Max retries ({max_retries}) exceeded: {exc}") from exc

            elapsed = time.monotonic() - start
            if elapsed >= total_timeout:
                raise RetryableError(f"Total timeout ({total_timeout}s) exceeded: {exc}") from exc

            retry_after = _get_retry_after(exc)
            if retry_after is not None:
                delay = retry_after
            else:
                delay = _compute_backoff(attempt, base_delay, max_delay)

            remaining = total_timeout - elapsed
            delay = min(delay, remaining)

            logger.warning(
                "Attempt %d/%d failed: %s. Retrying in %.2fs...",
                attempt + 1,
                max_retries + 1,
                exc,
                delay,
            )
            await asyncio.sleep(delay)

    raise RetryableError("Unexpected retry loop exit")


def sync_retry_call(
    fn: Callable[..., T],
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    total_timeout: float = 300.0,
) -> T:
    start = time.monotonic()

    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as exc:
            error_cls = classify_error(exc)

            if error_cls == FatalError:
                raise FatalError(str(exc)) from exc

            if issubclass(error_cls, _NO_RETRY_TYPES):
                raise error_cls(str(exc)) from exc

            if attempt >= max_retries:
                raise RetryableError(f"Max retries ({max_retries}) exceeded: {exc}") from exc

            elapsed = time.monotonic() - start
            if elapsed >= total_timeout:
                raise RetryableError(f"Total timeout ({total_timeout}s) exceeded: {exc}") from exc

            retry_after = _get_retry_after(exc)
            if retry_after is not None:
                delay = retry_after
            else:
                delay = _compute_backoff(attempt, base_delay, max_delay)

            remaining = total_timeout - elapsed
            delay = min(delay, remaining)

            logger.warning(
                "Attempt %d/%d failed: %s. Retrying in %.2fs...",
                attempt + 1,
                max_retries + 1,
                exc,
                delay,
            )
            time.sleep(delay)

    raise RetryableError("Unexpected retry loop exit")
