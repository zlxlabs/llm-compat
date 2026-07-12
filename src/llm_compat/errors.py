from __future__ import annotations

import httpx

from ._trace import CallTrace

_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_FATAL_STATUS_CODES: frozenset[int] = frozenset({400, 401, 403, 404})

_TRUNCATION_PATTERNS = ("unterminated string", "unexpected end")

_CONTENT_POLICY_STATUS_CODES: frozenset[int] = frozenset({400, 403, 451, 500})
_CONTENT_POLICY_BODY_KEYWORDS: tuple[str, ...] = (
    "content_policy",
    "content policy",
    "content_filter",
    "content filter",
    "sensitive_words",
    "sensitive",
    "moderation",
    "blocked",
)


class LLMError(Exception):
    pass


class LLMCallError(LLMError):
    """Base class for stable operational errors produced by an LLM call."""

    default_error_kind = "unknown"

    def __init__(
        self,
        message: str,
        *,
        error_kind: str | None = None,
        http_status: int | None = None,
        trace: CallTrace | None = None,
    ) -> None:
        super().__init__(message)
        self.error_kind = error_kind or self.default_error_kind
        self.http_status = http_status
        self.trace = trace


class RetryableError(LLMCallError):
    default_error_kind = "unknown"


class FatalError(LLMCallError):
    default_error_kind = "invalid_request"


class TimeoutError(RetryableError):
    default_error_kind = "timeout"


class TruncationError(RetryableError):
    default_error_kind = "json_parse"


class ContentPolicyError(LLMCallError):
    def __init__(
        self,
        message: str,
        *,
        attempted_models: list[str] | None = None,
        raw_content: str = "",
        original_model: str = "",
        error_kind: str | None = None,
        http_status: int | None = None,
        trace: CallTrace | None = None,
    ) -> None:
        super().__init__(
            message,
            error_kind=error_kind or "content_policy",
            http_status=http_status,
            trace=trace,
        )
        self.attempted_models: list[str] = attempted_models or []
        self.raw_content = raw_content
        self.original_model = original_model


class SkipRequestError(LLMError):
    pass


class JSONParseError(LLMCallError):
    def __init__(
        self,
        message: str,
        *,
        raw_content: str = "",
        model: str = "",
        request_id: str = "",
        error_kind: str | None = None,
        http_status: int | None = None,
        trace: CallTrace | None = None,
    ) -> None:
        super().__init__(
            message,
            error_kind=error_kind or "json_parse",
            http_status=http_status,
            trace=trace,
        )
        self.raw_content = raw_content
        self.model = model
        self.request_id = request_id


def describe_error(error: Exception) -> tuple[str, int | None]:
    """Return stable metadata without serializing error text or the cause object."""

    current: BaseException | None = error
    while current is not None:
        if isinstance(current, httpx.HTTPStatusError):
            status = current.response.status_code
            if status == 400:
                return "invalid_request", status
            if status == 401:
                return "authentication", status
            if status == 403:
                return "permission_denied", status
            if status == 404:
                return "model_not_found", status
            if status == 429:
                return "rate_limited", status
            if status >= 500:
                return "upstream_server_error", status
            return "unknown", status
        if isinstance(current, httpx.TimeoutException):
            return "timeout", None
        if isinstance(current, httpx.NetworkError):
            return "network_error", None
        current = current.__cause__

    if isinstance(error, LLMCallError):
        return error.error_kind, error.http_status
    return "unknown", None


def classify_error(error: Exception) -> type[LLMError]:
    if isinstance(error, httpx.TimeoutException):
        return TimeoutError

    if isinstance(error, httpx.HTTPStatusError):
        code = error.response.status_code
        if code in _CONTENT_POLICY_STATUS_CODES:
            body = error.response.text.lower()
            if any(kw in body for kw in _CONTENT_POLICY_BODY_KEYWORDS):
                return ContentPolicyError
        if code in _FATAL_STATUS_CODES:
            return FatalError
        if code in _RETRYABLE_STATUS_CODES:
            return RetryableError
        return RetryableError

    if isinstance(error, httpx.NetworkError):
        return RetryableError

    msg = str(error).lower()
    for pattern in _TRUNCATION_PATTERNS:
        if pattern in msg:
            return TruncationError

    return RetryableError
