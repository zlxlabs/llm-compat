"""Passthrough of finish_reason, reasoning_tokens, and refused-attempt layers."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel
from pytest_httpx import HTTPXMock

from llm_compat import ContentPolicyError, LLMClient, SyncLLMClient

MESSAGES = [{"role": "user", "content": "hello"}]


class TagResult(BaseModel):
    tags: list[str]


def _issue25_payload(
    *,
    content: str = "truncated output",
    finish_reason: str | None = "length",
    include_finish_reason: bool = True,
    include_details: bool = True,
    reasoning_tokens: int | None = 4431,
) -> dict[str, Any]:
    """Faithful reproduction of the #25 production truncation payload."""
    choice: dict[str, Any] = {
        "index": 0,
        "message": {"role": "assistant", "content": content},
    }
    if include_finish_reason:
        choice["finish_reason"] = finish_reason
    usage: dict[str, Any] = {
        "prompt_tokens": 2095,
        "completion_tokens": 5046,
    }
    if include_details:
        details: dict[str, Any] = {}
        if reasoning_tokens is not None:
            details["reasoning_tokens"] = reasoning_tokens
        usage["completion_tokens_details"] = details
    return {
        "id": "chatcmpl-issue25",
        "object": "chat.completion",
        "model": "test-model",
        "choices": [choice],
        "usage": usage,
    }


def _assert_issue25_usage(result: Any) -> None:
    assert result.usage is not None
    assert result.usage.prompt_tokens == 2095
    assert result.usage.completion_tokens == 5046
    assert result.usage.total_tokens == 0


async def test_chat_exposes_length_finish_reason_and_reasoning_tokens(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(json=_issue25_payload())
    async with LLMClient(base_url="http://test/v1", api_key="secret") as client:
        result = await client.chat("gpt-4o", MESSAGES)

    assert result.finish_reason == "length"
    _assert_issue25_usage(result)
    assert result.usage.reasoning_tokens == 4431


async def test_missing_finish_reason_is_none_not_a_guess(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=_issue25_payload(include_finish_reason=False))
    async with LLMClient(base_url="http://test/v1", api_key="secret") as client:
        result = await client.chat("gpt-4o", MESSAGES)

    assert result.finish_reason is None
    assert result.finish_reason != ""
    assert result.finish_reason != "stop"


async def test_missing_completion_token_details_yields_zero_reasoning(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(json=_issue25_payload(include_details=False))
    async with LLMClient(base_url="http://test/v1", api_key="secret") as client:
        result = await client.chat("gpt-4o", MESSAGES)

    _assert_issue25_usage(result)
    assert result.usage.reasoning_tokens == 0


async def test_chat_json_exposes_the_same_passthrough_fields(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        json=_issue25_payload(content='{"tags": ["truncated"]}')
    )
    async with LLMClient(base_url="http://test/v1", api_key="secret") as client:
        result = await client.chat_json("gpt-4o", MESSAGES, schema=TagResult)

    assert result.finish_reason == "length"
    _assert_issue25_usage(result)
    assert result.usage.reasoning_tokens == 4431
    assert result.parsed is not None
    assert result.parsed.tags == ["truncated"]


def test_sync_chat_and_chat_json_match_async_passthrough(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=_issue25_payload())
    with SyncLLMClient(base_url="http://test/v1", api_key="secret") as client:
        chat_result = client.chat("gpt-4o", MESSAGES)
    assert chat_result.finish_reason == "length"
    _assert_issue25_usage(chat_result)
    assert chat_result.usage.reasoning_tokens == 4431

    httpx_mock.add_response(
        json=_issue25_payload(content='{"tags": ["truncated"]}')
    )
    with SyncLLMClient(base_url="http://test/v1", api_key="secret") as client:
        json_result = client.chat_json("gpt-4o", MESSAGES, schema=TagResult)
    assert json_result.finish_reason == "length"
    _assert_issue25_usage(json_result)
    assert json_result.usage.reasoning_tokens == 4431

    httpx_mock.add_response(json=_issue25_payload(include_finish_reason=False))
    with SyncLLMClient(base_url="http://test/v1", api_key="secret") as client:
        missing_finish = client.chat("gpt-4o", MESSAGES)
    assert missing_finish.finish_reason is None

    httpx_mock.add_response(json=_issue25_payload(include_details=False))
    with SyncLLMClient(base_url="http://test/v1", api_key="secret") as client:
        missing_details = client.chat("gpt-4o", MESSAGES)
    assert missing_details.usage is not None
    assert missing_details.usage.reasoning_tokens == 0


async def test_refused_attempt_records_detection_layer_and_finish_reason(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(
        json=_issue25_payload(
            content="I cannot help with that",
            finish_reason="stop",
            include_details=False,
        )
    )
    httpx_mock.add_response(json=_issue25_payload(content="ok", finish_reason="stop"))
    async with LLMClient(
        base_url="http://test/v1",
        api_key="secret",
        content_fallbacks={"deepseek-*": ["gpt-4o"]},
        refusal_keywords=["cannot help"],
    ) as client:
        result = await client.chat("deepseek-v4", MESSAGES)

    assert result.trace is not None
    refused, succeeded = result.trace.model_attempts
    assert refused.detection_layer == "text_pattern"
    assert refused.finish_reason == "stop"
    serialized = refused.to_dict()
    assert serialized["detection_layer"] == "text_pattern"
    assert serialized["finish_reason"] == "stop"
    json.dumps(serialized)

    assert succeeded.detection_layer is None
    assert succeeded.finish_reason is None
    success_keys = succeeded.to_dict()
    assert success_keys.keys() >= {
        "model",
        "provider",
        "json_mode",
        "trigger",
        "outcome",
        "error_kind",
        "http_status",
        "latency_ms",
        "response_classification",
    }
    assert success_keys["response_classification"] is None
    json.dumps(success_keys)


async def test_structured_refusal_attempt_uses_provider_layer(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        json=_issue25_payload(content="partial", finish_reason="content_filter")
    )
    async with LLMClient(base_url="http://test/v1", api_key="secret") as client:
        with pytest.raises(ContentPolicyError) as exc_info:
            await client.chat("gpt-4o", MESSAGES)

    trace = exc_info.value.trace
    assert trace is not None
    attempt = trace.model_attempts[0]
    assert attempt.detection_layer == "structured_signal"
    assert attempt.finish_reason == "content_filter"
    json.dumps(attempt.to_dict())


async def test_http_policy_error_attempt_uses_http_error_layer(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        status_code=451,
        json={"error": {"message": "content policy violation"}},
    )
    async with LLMClient(
        base_url="http://test/v1", api_key="secret", max_retries=0
    ) as client:
        with pytest.raises(ContentPolicyError) as exc_info:
            await client.chat("gpt-4o", MESSAGES)

    trace = exc_info.value.trace
    assert trace is not None
    attempt = trace.model_attempts[0]
    assert attempt.detection_layer == "http_error"
    assert attempt.finish_reason is None
    json.dumps(attempt.to_dict())


async def test_normal_success_attempt_keeps_new_fields_none(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(json=_issue25_payload())
    async with LLMClient(base_url="http://test/v1", api_key="secret") as client:
        result = await client.chat("gpt-4o", MESSAGES)

    assert result.finish_reason == "length"
    assert result.trace is not None
    attempt = result.trace.model_attempts[0]
    assert attempt.detection_layer is None
    assert attempt.finish_reason is None
    serialized = attempt.to_dict()
    assert serialized["model"] == "gpt-4o"
    assert serialized["outcome"] == "response_received"
    assert serialized["detection_layer"] is None
    assert serialized["finish_reason"] is None
    json.dumps(serialized)
