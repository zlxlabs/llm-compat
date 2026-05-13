from __future__ import annotations

import httpx

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


class RetryableError(LLMError):
    pass


class FatalError(LLMError):
    pass


class TimeoutError(RetryableError):
    pass


class TruncationError(RetryableError):
    pass


class ContentPolicyError(LLMError):
    def __init__(
        self,
        message: str,
        *,
        attempted_models: list[str] | None = None,
        raw_content: str = "",
        original_model: str = "",
    ) -> None:
        super().__init__(message)
        self.attempted_models: list[str] = attempted_models or []
        self.raw_content = raw_content
        self.original_model = original_model


class SkipRequestError(LLMError):
    pass


class JSONParseError(LLMError):
    def __init__(
        self,
        message: str,
        *,
        raw_content: str = "",
        model: str = "",
        request_id: str = "",
    ) -> None:
        super().__init__(message)
        self.raw_content = raw_content
        self.model = model
        self.request_id = request_id


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
