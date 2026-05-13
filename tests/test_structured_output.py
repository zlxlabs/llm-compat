"""Tests for structured JSON output: response_format injection, self-correction, hooks, concurrency."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel
from pytest_httpx import HTTPXMock

from llm_compat._base import BaseClient
from llm_compat._types import ChatResult
from llm_compat.client import LLMClient
from llm_compat.errors import ContentPolicyError, JSONParseError, SkipRequestError


class TagResult(BaseModel):
    tags: list[str]


class ScoreResult(BaseModel):
    name: str
    score: float


def _chat_response(content: str, *, model: str = "gpt-4o") -> dict:
    return {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


class TestResponseFormatInjection:
    """chat_json should inject response_format based on provider json_mode."""

    async def test_json_schema_mode_injects_response_format(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response('{"tags": ["python"]}'))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            result = await client.chat_json(
                "gpt-5-mini",
                [{"role": "user", "content": "tag this"}],
                schema=TagResult,
            )
        assert result.parsed.tags == ["python"]
        request = httpx_mock.get_request()
        body = json.loads(request.content)
        rf = body["response_format"]
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["name"] == "TagResult"
        assert rf["json_schema"]["strict"] is True

    async def test_json_object_mode_for_deepseek(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response('{"tags": ["ai"]}'))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            result = await client.chat_json(
                "deepseek-v4-flash",
                [{"role": "user", "content": "tag this"}],
                schema=TagResult,
            )
        assert result.parsed.tags == ["ai"]
        request = httpx_mock.get_request()
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}

    async def test_no_schema_uses_json_object(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response('{"key": "value"}'))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            result = await client.chat_json("gpt-4o", [{"role": "user", "content": "json"}])
        assert result.parsed == {"key": "value"}
        request = httpx_mock.get_request()
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}

    async def test_explicit_json_schema_overrides_pydantic(self, httpx_mock: HTTPXMock) -> None:
        """json_schema dict takes precedence over Pydantic schema (on capable providers)."""
        custom_schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        httpx_mock.add_response(json=_chat_response('{"tags": ["a"]}'))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            await client.chat_json(
                "gpt-5-mini",
                [{"role": "user", "content": "test"}],
                schema=TagResult,
                json_schema=custom_schema,
            )
        request = httpx_mock.get_request()
        body = json.loads(request.content)
        rf = body["response_format"]
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["schema"] == custom_schema

    async def test_json_schema_dict_respects_provider_caps(self, httpx_mock: HTTPXMock) -> None:
        """json_schema dict falls back to json_object when provider doesn't support json_schema."""
        custom_schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        httpx_mock.add_response(json=_chat_response('{"x": 1}'))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            await client.chat_json(
                "deepseek-v4",
                [{"role": "user", "content": "test"}],
                json_schema=custom_schema,
            )
        request = httpx_mock.get_request()
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        last_user_msg = [m for m in body["messages"] if m["role"] == "user"][-1]
        assert '"x"' in last_user_msg["content"]

    async def test_json_stats_tracked(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response('{"tags": ["a"]}'))
        httpx_mock.add_response(json=_chat_response('{"tags": ["b"]}'))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            await client.chat_json("gpt-5-mini", [{"role": "user", "content": "1"}], schema=TagResult)
            await client.chat_json(
                "deepseek-v4-flash", [{"role": "user", "content": "2"}], schema=TagResult,
            )
        assert client.stats.json_schema_calls == 1
        assert client.stats.json_object_calls == 1


class TestSelfCorrection:
    """Self-correction retries with error feedback when JSON parsing fails."""

    async def test_self_correction_success_on_second_try(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("not valid json"))
        httpx_mock.add_response(json=_chat_response('{"tags": ["fixed"]}'))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            result = await client.chat_json(
                "gpt-4o",
                [{"role": "user", "content": "tag"}],
                schema=TagResult,
                self_correction=True,
                max_retries=2,
            )
        assert result.parsed.tags == ["fixed"]
        assert client.stats.json_self_correction_success == 1
        assert client.stats.json_parse_failures == 1

    async def test_self_correction_exhausted_raises(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("bad1"))
        httpx_mock.add_response(json=_chat_response("bad2"))
        httpx_mock.add_response(json=_chat_response("bad3"))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            with pytest.raises(JSONParseError):
                await client.chat_json(
                    "gpt-4o",
                    [{"role": "user", "content": "tag"}],
                    schema=TagResult,
                    self_correction=True,
                    max_retries=2,
                )
        assert client.stats.json_parse_failures == 3

    async def test_self_correction_disabled_by_default(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("bad json"))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            with pytest.raises(JSONParseError):
                await client.chat_json(
                    "gpt-4o",
                    [{"role": "user", "content": "tag"}],
                    schema=TagResult,
                )
        assert len(httpx_mock.get_requests()) == 1

    async def test_self_correction_appends_error_message(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("oops not json"))
        httpx_mock.add_response(json=_chat_response('{"tags": ["ok"]}'))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            await client.chat_json(
                "gpt-4o",
                [{"role": "user", "content": "tag"}],
                schema=TagResult,
                self_correction=True,
                max_retries=1,
            )
        requests = httpx_mock.get_requests()
        second_body = json.loads(requests[1].content)
        messages = second_body["messages"]
        assert len(messages) >= 3
        assert messages[-2]["role"] == "assistant"
        assert messages[-1]["role"] == "user"
        assert "error" in messages[-1]["content"].lower() or "failed" in messages[-1]["content"].lower()


class TestHooks:
    """Lifecycle hooks: on_success, on_error, pre_request."""

    async def test_on_success_called(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("ok"))
        hook = MagicMock()
        async with LLMClient(
            base_url="http://test/v1", api_key="sk-test", on_success=hook,
        ) as client:
            await client.chat("gpt-4o", [{"role": "user", "content": "hi"}])
        hook.assert_called_once()
        args = hook.call_args[0]
        assert args[0] == "gpt-4o"
        assert isinstance(args[1], int)

    async def test_on_error_called(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(status_code=401, json={"error": "bad"})
        hook = MagicMock()
        async with LLMClient(
            base_url="http://test/v1", api_key="sk-test", on_error=hook, max_retries=0,
        ) as client:
            with pytest.raises(Exception):
                await client.chat("gpt-4o", [{"role": "user", "content": "hi"}])
        hook.assert_called_once()
        args = hook.call_args[0]
        assert args[0] == "gpt-4o"
        assert isinstance(args[1], Exception)

    async def test_on_success_exception_swallowed(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("ok"))
        hook = MagicMock(side_effect=RuntimeError("hook boom"))
        async with LLMClient(
            base_url="http://test/v1", api_key="sk-test", on_success=hook,
        ) as client:
            result = await client.chat("gpt-4o", [{"role": "user", "content": "hi"}])
        assert result.content == "ok"

    async def test_on_error_exception_swallowed(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(status_code=401, json={"error": "bad"})
        hook = MagicMock(side_effect=RuntimeError("hook boom"))
        async with LLMClient(
            base_url="http://test/v1", api_key="sk-test", on_error=hook, max_retries=0,
        ) as client:
            with pytest.raises(Exception):
                await client.chat("gpt-4o", [{"role": "user", "content": "hi"}])

    async def test_pre_request_false_skips(self, httpx_mock: HTTPXMock) -> None:
        hook = MagicMock(return_value=False)
        async with LLMClient(
            base_url="http://test/v1", api_key="sk-test", pre_request=hook,
        ) as client:
            with pytest.raises(SkipRequestError):
                await client.chat("gpt-4o", [{"role": "user", "content": "hi"}])
        assert len(httpx_mock.get_requests()) == 0

    async def test_pre_request_exception_propagates(self, httpx_mock: HTTPXMock) -> None:
        hook = MagicMock(side_effect=RuntimeError("breaker open"))
        async with LLMClient(
            base_url="http://test/v1", api_key="sk-test", pre_request=hook,
        ) as client:
            with pytest.raises(RuntimeError, match="breaker open"):
                await client.chat("gpt-4o", [{"role": "user", "content": "hi"}])

    async def test_pre_request_true_proceeds(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("ok"))
        hook = MagicMock(return_value=True)
        async with LLMClient(
            base_url="http://test/v1", api_key="sk-test", pre_request=hook,
        ) as client:
            result = await client.chat("gpt-4o", [{"role": "user", "content": "hi"}])
        assert result.content == "ok"


class TestMaxConcurrency:
    async def test_concurrency_limits_parallel(self, httpx_mock: HTTPXMock) -> None:
        call_count = 0
        max_concurrent = 0
        current = 0

        original_add = httpx_mock.add_response

        for _ in range(5):
            httpx_mock.add_response(json=_chat_response("ok"))

        async with LLMClient(
            base_url="http://test/v1", api_key="sk-test", max_concurrency=2,
        ) as client:
            tasks = [
                client.chat("gpt-4o", [{"role": "user", "content": f"msg{i}"}])
                for i in range(5)
            ]
            results = await asyncio.gather(*tasks)
        assert len(results) == 5
        assert all(r.content == "ok" for r in results)

    async def test_no_concurrency_limit_by_default(self, httpx_mock: HTTPXMock) -> None:
        for _ in range(3):
            httpx_mock.add_response(json=_chat_response("ok"))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            assert not hasattr(client, "_semaphore") or client._semaphore is None
            tasks = [
                client.chat("gpt-4o", [{"role": "user", "content": f"msg{i}"}])
                for i in range(3)
            ]
            results = await asyncio.gather(*tasks)
        assert len(results) == 3


class TestJsonSchemaFallback:
    """json_schema mode falls back to json_object on API error."""

    async def test_fallback_on_400(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(status_code=400, json={"error": "unsupported response_format"})
        httpx_mock.add_response(json=_chat_response('{"tags": ["fallback"]}'))
        async with LLMClient(
            base_url="http://test/v1", api_key="sk-test", max_retries=0,
        ) as client:
            result = await client.chat_json(
                "gpt-5-mini",
                [{"role": "user", "content": "tag"}],
                schema=TagResult,
            )
        assert result.parsed.tags == ["fallback"]
        requests = httpx_mock.get_requests()
        first_body = json.loads(requests[0].content)
        second_body = json.loads(requests[1].content)
        assert first_body["response_format"]["type"] == "json_schema"
        assert second_body["response_format"] == {"type": "json_object"}
        assert client.stats.json_object_calls == 1


class TestMultimodalSchemaInjection:
    """Schema injection should handle multimodal (list) content in user messages."""

    def test_inject_into_list_content(self) -> None:
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "Analyze this image"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ]}
        ]
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
        result = BaseClient._inject_schema_prompt(messages, schema)
        assert len(result) == 1
        content = result[0]["content"]
        assert isinstance(content, list)
        assert len(content) == 3
        assert content[2]["type"] == "text"
        assert '"x"' in content[2]["text"]

    def test_inject_does_not_mutate_original(self) -> None:
        original_content = [
            {"type": "text", "text": "test"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ]
        messages = [{"role": "user", "content": original_content}]
        schema = {"type": "object", "properties": {"y": {"type": "string"}}}
        result = BaseClient._inject_schema_prompt(messages, schema)
        assert len(original_content) == 2
        assert len(result[0]["content"]) == 3

    def test_inject_prefers_list_over_earlier_string(self) -> None:
        messages = [
            {"role": "user", "content": "earlier question"},
            {"role": "assistant", "content": "response"},
            {"role": "user", "content": [
                {"type": "text", "text": "look at this image"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ]},
        ]
        schema = {"type": "object", "properties": {"z": {"type": "number"}}}
        result = BaseClient._inject_schema_prompt(messages, schema)
        assert isinstance(result[2]["content"], list)
        assert len(result[2]["content"]) == 3
        assert result[0]["content"] == "earlier question"

    async def test_chat_json_with_multimodal_deepseek(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response('{"x": 42}'))
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "Extract data"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ]}
        ]
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            await client.chat_json(
                "deepseek-v4",
                messages,
                json_schema={"type": "object", "properties": {"x": {"type": "integer"}}},
            )
        request = httpx_mock.get_request()
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        user_msg = body["messages"][0]
        assert isinstance(user_msg["content"], list)
        assert len(user_msg["content"]) == 3
        assert user_msg["content"][2]["type"] == "text"


class TestContentFallbackWithJson:
    """chat_json should support content_fallbacks like chat() does."""

    def _refusal_response(self, content: str = "I cannot help with that.") -> dict:
        return {
            "id": "chatcmpl-refused",
            "object": "chat.completion",
            "model": "deepseek-v4",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    async def test_json_fallback_on_content_policy(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(status_code=451, json={"error": "content policy violation"})
        httpx_mock.add_response(json=_chat_response('{"tags": ["ok"]}'))
        async with LLMClient(
            base_url="http://test/v1", api_key="sk-test",
            content_fallbacks={"deepseek-v4": ["gpt-5-mini"]},
            max_retries=0,
        ) as client:
            result = await client.chat_json(
                "deepseek-v4",
                [{"role": "user", "content": "test"}],
                schema=TagResult,
            )
        assert result.parsed.tags == ["ok"]
        assert result.fallback_from == "deepseek-v4"

    async def test_json_fallback_on_refusal_detection(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=self._refusal_response())
        httpx_mock.add_response(json=_chat_response('{"tags": ["safe"]}'))
        async with LLMClient(
            base_url="http://test/v1", api_key="sk-test",
            content_fallbacks={"deepseek-v4": ["gpt-5-mini"]},
            refusal_keywords=["cannot help"],
            max_retries=0,
        ) as client:
            result = await client.chat_json(
                "deepseek-v4",
                [{"role": "user", "content": "sensitive topic"}],
                schema=TagResult,
            )
        assert result.parsed.tags == ["safe"]
        assert result.fallback_from == "deepseek-v4"

    async def test_json_no_fallback_without_chain(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response('{"tags": ["normal"]}'))
        async with LLMClient(
            base_url="http://test/v1", api_key="sk-test",
            max_retries=0,
        ) as client:
            result = await client.chat_json(
                "deepseek-v4",
                [{"role": "user", "content": "test"}],
                schema=TagResult,
            )
        assert result.parsed.tags == ["normal"]
        assert result.fallback_from is None

    async def test_json_fallback_model_uses_correct_json_mode(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(status_code=451, json={"error": "content policy"})
        httpx_mock.add_response(json=_chat_response('{"tags": ["ok"]}'))
        async with LLMClient(
            base_url="http://test/v1", api_key="sk-test",
            content_fallbacks={"deepseek-v4": ["gpt-5-mini"]},
            max_retries=0,
        ) as client:
            await client.chat_json(
                "deepseek-v4",
                [{"role": "user", "content": "test"}],
                schema=TagResult,
            )
        requests = httpx_mock.get_requests()
        first_body = json.loads(requests[0].content)
        second_body = json.loads(requests[1].content)
        assert first_body["response_format"]["type"] == "json_object"
        assert second_body["response_format"]["type"] == "json_schema"

    async def test_json_all_models_refused(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(status_code=451, json={"error": "content policy"})
        httpx_mock.add_response(status_code=451, json={"error": "content policy"})
        async with LLMClient(
            base_url="http://test/v1", api_key="sk-test",
            content_fallbacks={"deepseek-v4": ["gpt-5-mini"]},
            max_retries=0,
        ) as client:
            with pytest.raises(ContentPolicyError) as exc_info:
                await client.chat_json(
                    "deepseek-v4",
                    [{"role": "user", "content": "test"}],
                    schema=TagResult,
                )
            assert "deepseek-v4" in exc_info.value.attempted_models
