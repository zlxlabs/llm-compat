"""Regression matrix for trace collection in shared orchestration."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import BaseModel
from pytest_httpx import HTTPXMock

from llm_compat import ContentPolicyError, FatalError, JSONParseError, LLMClient, SyncLLMClient
from llm_compat.sensitive import SensitiveDetector

MESSAGES = [{"role": "user", "content": "hello"}]


class TagResult(BaseModel):
    tags: list[str]


def _chat_response(content: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-123",
        "model": "test-model",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }


def _semantic_trace(trace: Any) -> dict[str, Any]:
    data = trace.to_dict()
    data.pop("request_id")
    data.pop("started_at")
    data.pop("latency_ms")
    for attempt in data["model_attempts"]:
        attempt.pop("latency_ms")
    return data


def _cause_types(error: BaseException) -> list[type[BaseException]]:
    result: list[type[BaseException]] = []
    current: BaseException | None = error
    while current is not None:
        result.append(type(current))
        current = current.__cause__
    return result


async def test_basic_success_and_sync_trace_have_the_same_semantics(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(json=_chat_response("async"))
    async with LLMClient(base_url="http://test/v1", api_key="secret") as client:
        async_result = await client.chat("gpt-4o", MESSAGES)

    httpx_mock.add_response(json=_chat_response("sync"))
    with SyncLLMClient(base_url="http://test/v1", api_key="secret") as client:
        sync_result = client.chat("gpt-4o", MESSAGES)

    assert async_result.trace is not None
    assert sync_result.trace is not None
    assert _semantic_trace(async_result.trace) == _semantic_trace(sync_result.trace)
    assert async_result.trace.final_outcome == "success"
    assert async_result.trace.final_model == "gpt-4o"
    assert [item.model for item in async_result.trace.model_attempts] == ["gpt-4o"]


@pytest.mark.parametrize(
    ("detector", "messages", "reason"),
    [
        (SensitiveDetector(words=["敏感词"]), MESSAGES, "prescan_miss"),
        (SensitiveDetector(words=[]), MESSAGES, "prescan_unavailable"),
    ],
)
async def test_prescan_miss_and_unavailable_are_explicit(
    httpx_mock: HTTPXMock,
    detector: SensitiveDetector,
    messages: list[dict[str, str]],
    reason: str,
) -> None:
    httpx_mock.add_response(json=_chat_response("ok"))
    async with LLMClient(
        base_url="http://test/v1",
        api_key="secret",
        content_fallbacks={"deepseek-*": ["gpt-4o"]},
        sensitive_detector=detector,
    ) as client:
        result = await client.chat("deepseek-v4", messages)

    assert result.trace is not None
    assert result.trace.route_decisions[0].reason == reason


async def test_prescan_hit_skips_primary_without_creating_an_attempt(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(json=_chat_response("fallback"))
    async with LLMClient(
        base_url="http://test/v1",
        api_key="secret",
        content_fallbacks={"deepseek-*": ["gpt-4o"]},
        sensitive_detector=SensitiveDetector(words=["敏感词"]),
    ) as client:
        result = await client.chat(
            "deepseek-v4", [{"role": "user", "content": "包含敏感词"}]
        )

    assert result.trace is not None
    assert result.trace.route_decisions[0].action == "skipped"
    assert result.trace.route_decisions[0].reason == "sensitive_match"
    assert [item.model for item in result.trace.model_attempts] == ["gpt-4o"]
    assert result.trace.model_attempts[0].trigger == "sensitive_prescan"


async def test_refusal_after_prescan_labels_later_model_as_content_fallback(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(json=_chat_response("I cannot help"))
    httpx_mock.add_response(json=_chat_response("ok"))
    async with LLMClient(
        base_url="http://test/v1",
        api_key="secret",
        content_fallbacks={"deepseek-*": ["gpt-4o", "gemini-2.5-flash"]},
        sensitive_detector=SensitiveDetector(words=["敏感词"]),
        refusal_keywords=["cannot help"],
    ) as client:
        result = await client.chat(
            "deepseek-v4", [{"role": "user", "content": "包含敏感词"}]
        )

    assert result.trace is not None
    assert [item.model for item in result.trace.model_attempts] == [
        "gpt-4o",
        "gemini-2.5-flash",
    ]
    assert [item.trigger for item in result.trace.model_attempts] == [
        "sensitive_prescan",
        "content_fallback",
    ]


async def test_200_refusal_and_http_policy_error_are_distinct_attempts(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(json=_chat_response("I cannot help with that"))
    httpx_mock.add_response(
        status_code=451,
        json={"error": {"message": "content policy violation"}},
    )
    httpx_mock.add_response(json=_chat_response("ok"))
    async with LLMClient(
        base_url="http://test/v1",
        api_key="secret",
        content_fallbacks={"deepseek-*": ["gpt-4o", "gemini-2.5-flash"]},
        refusal_keywords=["cannot help"],
        max_retries=0,
    ) as client:
        result = await client.chat("deepseek-v4", MESSAGES)

    assert result.trace is not None
    attempts = result.trace.model_attempts
    assert [item.outcome for item in attempts] == [
        "response_received",
        "error",
        "response_received",
    ]
    assert attempts[0].response_classification == "content_policy"
    assert attempts[1].error_kind == "content_policy"
    assert attempts[1].http_status == 451
    assert result.trace.final_outcome == "success"


async def test_generic_400_is_terminal_and_does_not_downgrade_schema(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(status_code=400, json={"error": "unsupported account option"})
    async with LLMClient(
        base_url="http://test/v1", api_key="secret", max_retries=0
    ) as client:
        with pytest.raises(FatalError) as exc_info:
            await client.chat_json("gpt-5-mini", MESSAGES, schema=TagResult)

    assert len(httpx_mock.get_requests()) == 1
    error = exc_info.value
    assert error.error_kind == "invalid_request"
    assert error.http_status == 400
    assert error.trace is not None
    assert error.trace.final_outcome == "invalid_request"
    assert error.trace.model_attempts[0].json_mode == "json_schema"
    assert client.stats._errors_by_type == {"FatalError": 1}


@pytest.mark.parametrize(
    ("status", "error_kind"),
    [
        (401, "authentication"),
        (403, "permission_denied"),
        (404, "model_not_found"),
    ],
)
async def test_fatal_http_status_keeps_specific_error_kind(
    httpx_mock: HTTPXMock,
    status: int,
    error_kind: str,
) -> None:
    httpx_mock.add_response(status_code=status, json={"error": "failure"})
    async with LLMClient(
        base_url="http://test/v1", api_key="secret", max_retries=0
    ) as client:
        with pytest.raises(FatalError) as exc_info:
            await client.chat("gpt-4o", MESSAGES)

    error = exc_info.value
    assert error.error_kind == error_kind
    assert error.http_status == status
    assert error.trace is not None
    assert error.trace.final_outcome == error_kind
    assert error.trace.model_attempts[0].error_kind == error_kind


@pytest.mark.parametrize(
    "error_message",
    [
        "Invalid json_schema: required field 'name' is missing",
        "json_schema contains unsupported keyword 'oneOf'",
        "This model does not support keyword oneOf in json_schema",
        "This model does not support the json_schema keyword 'oneOf'",
    ],
)
async def test_malformed_schema_is_not_treated_as_unsupported_capability(
    httpx_mock: HTTPXMock,
    error_message: str,
) -> None:
    httpx_mock.add_response(
        status_code=400,
        json={"error": error_message},
    )
    async with LLMClient(
        base_url="http://test/v1", api_key="secret", max_retries=0
    ) as client:
        with pytest.raises(FatalError) as exc_info:
            await client.chat_json("gpt-5-mini", MESSAGES, schema=TagResult)

    assert len(httpx_mock.get_requests()) == 1
    assert exc_info.value.error_kind == "invalid_request"


@pytest.mark.parametrize(
    "error_message",
    [
        "response_format json_schema is unsupported",
        "response_format 'json_schema' is unsupported",
        'response_format "json_schema" is unsupported',
        "model does not currently support response_format json_schema",
        "Invalid parameter: 'response_format' of type 'json_schema' is not supported "
        "with this model",
    ],
)
async def test_explicit_schema_unsupported_downgrades_and_records_both_attempts(
    httpx_mock: HTTPXMock,
    error_message: str,
) -> None:
    httpx_mock.add_response(
        status_code=400,
        json={"error": {"message": error_message}},
    )
    httpx_mock.add_response(json=_chat_response('{"tags": ["ok"]}'))
    async with LLMClient(
        base_url="http://test/v1", api_key="secret", max_retries=0
    ) as client:
        result = await client.chat_json("gpt-5-mini", MESSAGES, schema=TagResult)

    assert result.trace is not None
    assert [item.json_mode for item in result.trace.model_attempts] == [
        "json_schema",
        "json_object",
    ]
    assert [item.trigger for item in result.trace.model_attempts] == [
        "primary",
        "schema_downgrade",
    ]
    assert result.trace.model_attempts[0].error_kind == "unsupported_response_format"


async def test_self_correction_success_and_parse_exhaustion_are_call_outcomes(
    httpx_mock: HTTPXMock,
) -> None:
    httpx_mock.add_response(json=_chat_response("bad"))
    httpx_mock.add_response(json=_chat_response('{"tags": ["ok"]}'))
    async with LLMClient(base_url="http://test/v1", api_key="secret") as client:
        result = await client.chat_json(
            "gpt-4o", MESSAGES, schema=TagResult, self_correction=True, max_retries=1
        )
    assert result.trace is not None
    assert [item.outcome for item in result.trace.model_attempts] == [
        "response_received",
        "response_received",
    ]
    assert result.trace.model_attempts[1].trigger == "self_correction"
    assert result.trace.final_outcome == "success"

    httpx_mock.add_response(json=_chat_response("bad"))
    async with LLMClient(base_url="http://test/v1", api_key="secret") as client:
        with pytest.raises(JSONParseError) as exc_info:
            await client.chat_json("gpt-4o", MESSAGES, schema=TagResult)
    error = exc_info.value
    assert error.trace is not None
    assert error.trace.final_outcome == "json_parse"
    assert error.trace.model_attempts[0].outcome == "response_received"


async def test_all_fallbacks_refused_keep_legacy_catch_trace_and_http_cause(
    httpx_mock: HTTPXMock,
) -> None:
    for _ in range(2):
        httpx_mock.add_response(
            status_code=451,
            json={"error": {"message": "content policy violation"}},
        )
    async with LLMClient(
        base_url="http://test/v1",
        api_key="secret",
        content_fallbacks={"deepseek-*": ["gpt-4o"]},
        max_retries=0,
    ) as client:
        with pytest.raises(ContentPolicyError) as exc_info:
            await client.chat("deepseek-v4", MESSAGES)

    error = exc_info.value
    assert error.http_status == 451
    assert error.trace is not None
    assert error.trace.final_outcome == "content_policy"
    assert [item.model for item in error.trace.model_attempts] == ["deepseek-v4", "gpt-4o"]
    assert httpx.HTTPStatusError in _cause_types(error)


async def test_unknown_driver_exception_is_not_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], int]:
        raise RuntimeError("driver bug")

    client = LLMClient(base_url="http://test/v1", api_key="secret")
    monkeypatch.setattr(client, "_single_chat", fail)
    with pytest.raises(RuntimeError, match="driver bug") as exc_info:
        await client.chat("gpt-4o", MESSAGES)
    assert not hasattr(exc_info.value, "trace")
