from __future__ import annotations

import httpx

from llm_compat.errors import (
    ContentPolicyError,
    FatalError,
    RetryableError,
    classify_error,
)


class TestContentPolicyError:
    def test_inherits_from_llm_error(self):
        from llm_compat.errors import LLMError
        err = ContentPolicyError("refused")
        assert isinstance(err, LLMError)

    def test_not_fatal_error(self):
        err = ContentPolicyError("refused")
        assert not isinstance(err, FatalError)

    def test_fields(self):
        err = ContentPolicyError(
            "refused",
            attempted_models=["deepseek-v4", "gpt-4.1-mini"],
            raw_content="我无法回答该问题",
            original_model="deepseek-v4",
        )
        assert err.attempted_models == ["deepseek-v4", "gpt-4.1-mini"]
        assert err.raw_content == "我无法回答该问题"
        assert err.original_model == "deepseek-v4"

    def test_default_fields(self):
        err = ContentPolicyError("refused")
        assert err.attempted_models == []
        assert err.raw_content == ""
        assert err.original_model == ""


def _make_http_error(status_code: int, body: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    response = httpx.Response(status_code, text=body, request=request)
    return httpx.HTTPStatusError(
        f"{status_code} Error",
        request=request,
        response=response,
    )


class TestClassifyContentPolicy:
    def test_400_with_content_policy_keyword(self):
        err = _make_http_error(400, '{"error": {"type": "content_policy_violation"}}')
        assert classify_error(err) == ContentPolicyError

    def test_400_with_content_filter_keyword(self):
        err = _make_http_error(400, '{"error": {"message": "content_filter triggered"}}')
        assert classify_error(err) == ContentPolicyError

    def test_400_with_sensitive_keyword(self):
        err = _make_http_error(400, '{"error": {"message": "sensitive content detected"}}')
        assert classify_error(err) == ContentPolicyError

    def test_400_with_moderation_keyword(self):
        err = _make_http_error(400, '{"error": {"message": "moderation flag"}}')
        assert classify_error(err) == ContentPolicyError

    def test_400_with_blocked_keyword(self):
        err = _make_http_error(400, '{"error": {"message": "request blocked"}}')
        assert classify_error(err) == ContentPolicyError

    def test_400_without_policy_keyword_is_fatal(self):
        err = _make_http_error(400, '{"error": {"message": "invalid request format"}}')
        assert classify_error(err) == FatalError

    def test_400_empty_body_is_fatal(self):
        err = _make_http_error(400, "")
        assert classify_error(err) == FatalError

    def test_403_with_content_keyword(self):
        err = _make_http_error(403, '{"error": {"message": "content policy violation"}}')
        assert classify_error(err) == ContentPolicyError

    def test_403_without_policy_keyword_is_fatal(self):
        err = _make_http_error(403, '{"error": {"message": "unauthorized access"}}')
        assert classify_error(err) == FatalError

    def test_401_always_fatal(self):
        err = _make_http_error(401, '{"error": {"message": "content_policy"}}')
        assert classify_error(err) == FatalError

    def test_404_always_fatal(self):
        err = _make_http_error(404, '{"error": {"message": "content_policy"}}')
        assert classify_error(err) == FatalError

    def test_500_with_sensitive_words_detected(self):
        err = _make_http_error(
            500,
            '{"error": {"message": "sensitive_words_detected (request id: abc123)", '
            '"type": "new_api_error", "code": "sensitive_words_detected"}}',
        )
        assert classify_error(err) == ContentPolicyError

    def test_500_without_policy_keyword_is_retryable(self):
        err = _make_http_error(500, '{"error": {"message": "internal server error"}}')
        assert classify_error(err) == RetryableError
