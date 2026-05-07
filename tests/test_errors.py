"""Tests for llm_compat.errors — error hierarchy and classification."""
from __future__ import annotations

import httpx
import pytest

from llm_compat.errors import (
    FatalError,
    JSONParseError,
    LLMError,
    RetryableError,
    TimeoutError,
    TruncationError,
    classify_error,
)


class TestErrorHierarchy:
    def test_retryable_is_llm_error(self):
        assert issubclass(RetryableError, LLMError)

    def test_fatal_is_llm_error(self):
        assert issubclass(FatalError, LLMError)

    def test_timeout_is_retryable(self):
        assert issubclass(TimeoutError, RetryableError)

    def test_truncation_is_retryable(self):
        assert issubclass(TruncationError, RetryableError)

    def test_json_parse_error_is_llm_error(self):
        assert issubclass(JSONParseError, LLMError)

    def test_json_parse_error_carries_context(self):
        err = JSONParseError(
            "parse failed",
            raw_content='{"broken',
            model="gpt-4o",
            request_id="abc123",
        )
        assert err.raw_content == '{"broken'
        assert err.model == "gpt-4o"
        assert err.request_id == "abc123"
        assert "parse failed" in str(err)


class TestClassifyError:
    @pytest.mark.parametrize(
        "exc,expected",
        [
            (httpx.TimeoutException("read timed out"), TimeoutError),
            (httpx.NetworkError("connection reset"), RetryableError),
            (
                httpx.HTTPStatusError(
                    "429",
                    request=httpx.Request("POST", "http://x"),
                    response=httpx.Response(429),
                ),
                RetryableError,
            ),
            (
                httpx.HTTPStatusError(
                    "500",
                    request=httpx.Request("POST", "http://x"),
                    response=httpx.Response(500),
                ),
                RetryableError,
            ),
            (
                httpx.HTTPStatusError(
                    "401",
                    request=httpx.Request("POST", "http://x"),
                    response=httpx.Response(401),
                ),
                FatalError,
            ),
            (
                httpx.HTTPStatusError(
                    "400",
                    request=httpx.Request("POST", "http://x"),
                    response=httpx.Response(400),
                ),
                FatalError,
            ),
            (
                httpx.HTTPStatusError(
                    "403",
                    request=httpx.Request("POST", "http://x"),
                    response=httpx.Response(403),
                ),
                FatalError,
            ),
            (
                httpx.HTTPStatusError(
                    "404",
                    request=httpx.Request("POST", "http://x"),
                    response=httpx.Response(404),
                ),
                FatalError,
            ),
        ],
    )
    def test_classify_httpx_errors(self, exc, expected):
        assert classify_error(exc) == expected

    @pytest.mark.parametrize("code", [500, 502, 503, 504])
    def test_5xx_is_retryable(self, code):
        exc = httpx.HTTPStatusError(
            str(code),
            request=httpx.Request("POST", "http://x"),
            response=httpx.Response(code),
        )
        assert classify_error(exc) == RetryableError

    def test_truncation_detected(self):
        err = Exception("unterminated string started at")
        assert classify_error(err) == TruncationError

    def test_unknown_error_is_retryable(self):
        assert classify_error(Exception("something weird")) == RetryableError
