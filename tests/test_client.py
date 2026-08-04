"""Tests for llm_compat.client — async LLM client with httpx mock."""
from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel
from pytest_httpx import HTTPXMock

from llm_compat._types import ChatResult
from llm_compat.client import LLMClient
from llm_compat.errors import FatalError, JSONParseError
from llm_compat.providers import describe_from_payload


class TagResult(BaseModel):
    tags: list[str]


def _chat_response(content: str, *, model: str = "gpt-4o") -> dict:
    return {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


class TestChat:
    async def test_basic_chat(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("hello world"))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            result = await client.chat("gpt-4o", [{"role": "user", "content": "hi"}])
        assert isinstance(result, ChatResult)
        assert result.content == "hello world"
        assert str(result) == "hello world"
        assert result.usage is not None
        assert result.usage.total_tokens == 30
        assert result.latency_ms > 0
        assert result.request_id != ""

    async def test_chat_with_reasoning_effort(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("thought deeply"))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            result = await client.chat(
                "deepseek-v4-flash",
                [{"role": "user", "content": "think"}],
                reasoning_effort="high",
            )
        assert result.content == "thought deeply"
        request = httpx_mock.get_request()
        body = json.loads(request.content)
        assert body["reasoning_effort"] == "high"

    async def test_chat_deepseek_disabled(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("quick"))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            await client.chat(
                "deepseek-v4-flash",
                [{"role": "user", "content": "hi"}],
                reasoning_effort="disabled",
            )
        request = httpx_mock.get_request()
        body = json.loads(request.content)
        assert body["thinking"]["type"] == "disabled"
        assert "extra_body" not in body
        assert "reasoning_effort" not in body
        assert describe_from_payload(body)["thinking_mode"] == "disabled"

    async def test_chat_deepseek_none_uses_disabled_wire_shape(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(json=_chat_response("quick"))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            await client.chat(
                "deepseek-v4-flash",
                [{"role": "user", "content": "hi"}],
                reasoning_effort="none",
            )
        body = json.loads(httpx_mock.get_request().content)
        assert body["thinking"] == {"type": "disabled"}
        assert "extra_body" not in body
        assert "reasoning_effort" not in body
        assert describe_from_payload(body)["thinking_mode"] == "disabled"

    async def test_chat_extra_body_is_merged_into_wire_body(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("ok"))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            await client.chat(
                "gpt-4o",
                [{"role": "user", "content": "hi"}],
                extra_body={"foo": "bar"},
            )
        body = json.loads(httpx_mock.get_request().content)
        assert body["foo"] == "bar"
        assert "extra_body" not in body

    async def test_chat_extra_body_thinking_is_dropped_with_warning(
        self, httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        httpx_mock.add_response(json=_chat_response("ok"))
        with caplog.at_level("WARNING"):
            async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
                await client.chat(
                    "deepseek-v4-flash",
                    [{"role": "user", "content": "hi"}],
                    extra_body={"thinking": {"type": "disabled"}},
                )
        body = json.loads(httpx_mock.get_request().content)
        assert "thinking" not in body
        assert "extra_body" not in body
        assert describe_from_payload(body)["thinking_mode"] != "disabled"
        assert any(
            record.getMessage()
            == "extra_body attempted to override reserved request field thinking; dropping value."
            for record in caplog.records
        )

    async def test_chat_extra_body_cannot_override_provider_thinking(
        self, httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        httpx_mock.add_response(json=_chat_response("ok"))
        with caplog.at_level("WARNING"):
            async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
                await client.chat(
                    "deepseek-v4-flash",
                    [{"role": "user", "content": "hi"}],
                    reasoning_effort="disabled",
                    extra_body={"thinking": {"type": "enabled"}},
                )
        body = json.loads(httpx_mock.get_request().content)
        assert body["thinking"] == {"type": "disabled"}
        assert "extra_body" not in body
        assert any(
            record.getMessage()
            == "extra_body attempted to override reserved request field thinking; dropping value."
            for record in caplog.records
        )

    @pytest.mark.parametrize("invalid_extra_body", [None, [], "", "not-a-dict"])
    async def test_chat_invalid_extra_body_is_dropped_with_warning(
        self,
        httpx_mock: HTTPXMock,
        caplog: pytest.LogCaptureFixture,
        invalid_extra_body: object,
    ) -> None:
        httpx_mock.add_response(json=_chat_response("ok"))
        with caplog.at_level("WARNING"):
            async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
                await client.chat(
                    "gpt-4o",
                    [{"role": "user", "content": "hi"}],
                    extra_body=invalid_extra_body,
                )
        body = json.loads(httpx_mock.get_request().content)
        assert "extra_body" not in body
        assert "extra_body must be a dict; dropping invalid value." in caplog.text

    async def test_chat_nested_extra_body_is_not_reintroduced(
        self, httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        httpx_mock.add_response(json=_chat_response("ok"))
        with caplog.at_level("WARNING"):
            async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
                await client.chat(
                    "gpt-4o",
                    [{"role": "user", "content": "hi"}],
                    extra_body={"extra_body": {"foo": "bar"}},
                )
        body = json.loads(httpx_mock.get_request().content)
        assert "extra_body" not in body
        assert "foo" not in body
        assert any(
            record.getMessage()
            == (
                "extra_body attempted to override reserved request field extra_body; "
                "dropping value."
            )
            for record in caplog.records
        )

    @pytest.mark.parametrize(
        ("reserved_key", "reserved_value"),
        [
            ("model", "overridden-model"),
            ("messages", [{"role": "assistant", "content": "overridden"}]),
            ("stream", True),
            ("extra_body", {"foo": "bar"}),
            ("thinking", {"type": "disabled"}),
        ],
    )
    async def test_chat_extra_body_reserved_keys_are_dropped(
        self,
        httpx_mock: HTTPXMock,
        caplog: pytest.LogCaptureFixture,
        reserved_key: str,
        reserved_value: object,
    ) -> None:
        messages = [{"role": "user", "content": "hi"}]
        httpx_mock.add_response(json=_chat_response("ok"))
        with caplog.at_level("WARNING"):
            async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
                result = await client.chat(
                    "gpt-4o",
                    messages,
                    extra_body={reserved_key: reserved_value},
                )
        body = json.loads(httpx_mock.get_request().content)
        assert result.content == "ok"
        assert body["model"] == "gpt-4o"
        assert body["messages"] == messages
        assert "stream" not in body
        assert "extra_body" not in body
        assert caplog.records
        assert any(
            record.getMessage()
            == f"extra_body attempted to override reserved request field {reserved_key}; "
            "dropping value."
            for record in caplog.records
        )

    async def test_401_raises_fatal(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(status_code=401, json={"error": "unauthorized"})
        async with LLMClient(
            base_url="http://test/v1", api_key="sk-bad", max_retries=0
        ) as client:
            with pytest.raises(FatalError):
                await client.chat("gpt-4o", [{"role": "user", "content": "hi"}])

    async def test_provider_and_model_in_result(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("ok", model="deepseek-v4-flash"))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            result = await client.chat("deepseek-v4-flash", [{"role": "user", "content": "hi"}])
        assert result.provider == "deepseek"
        assert result.model == "deepseek-v4-flash"


class TestChatJson:
    async def test_json_with_schema(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response('{"tags": ["python", "ai"]}'))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            result = await client.chat_json(
                "gpt-4o",
                [{"role": "user", "content": "tag this"}],
                schema=TagResult,
            )
        assert isinstance(result.parsed, TagResult)
        assert result.parsed.tags == ["python", "ai"]

    async def test_json_code_fence(self, httpx_mock: HTTPXMock) -> None:
        content = '```json\n{"tags": ["test"]}\n```'
        httpx_mock.add_response(json=_chat_response(content))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            result = await client.chat_json("gpt-4o", [], schema=TagResult)
        assert result.parsed.tags == ["test"]

    async def test_json_parse_error(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("not json"))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            with pytest.raises(JSONParseError) as exc_info:
                await client.chat_json("gpt-4o", [], schema=TagResult)
        assert exc_info.value.raw_content == "not json"
        assert exc_info.value.model == "gpt-4o"

    async def test_json_without_schema(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response('{"key": "value"}'))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            result = await client.chat_json("gpt-4o", [])
        assert result.parsed == {"key": "value"}

    async def test_json_deepseek_disabled_wire_shape(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response('{"key": "value"}'))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            result = await client.chat_json(
                "deepseek-v4-flash",
                [],
                reasoning_effort="disabled",
            )
        assert result.parsed == {"key": "value"}
        body = json.loads(httpx_mock.get_request().content)
        assert body["thinking"] == {"type": "disabled"}
        assert "extra_body" not in body
        assert "reasoning_effort" not in body


class TestChatStream:
    async def test_stream_yields_chunks(self, httpx_mock: HTTPXMock) -> None:
        sse_data = (
            b'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
            b"data: [DONE]\n\n"
        )
        httpx_mock.add_response(
            stream=httpx.ByteStream(sse_data),
            headers={"content-type": "text/event-stream"},
        )
        chunks = []
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            async for chunk in client.chat_stream("gpt-4o", [{"role": "user", "content": "hi"}]):
                chunks.append(chunk)
        assert "".join(chunks) == "hello"

    async def test_stream_deepseek_disabled_wire_shape(self, httpx_mock: HTTPXMock) -> None:
        sse_data = (
            b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            b"data: [DONE]\n\n"
        )
        httpx_mock.add_response(
            stream=httpx.ByteStream(sse_data),
            headers={"content-type": "text/event-stream"},
        )
        chunks = []
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            async for chunk in client.chat_stream(
                "deepseek-v4-flash",
                [{"role": "user", "content": "hi"}],
                reasoning_effort="disabled",
            ):
                chunks.append(chunk)
        assert "".join(chunks) == "ok"
        body = json.loads(httpx_mock.get_request().content)
        assert body["thinking"] == {"type": "disabled"}
        assert "extra_body" not in body


class TestChatImage:
    async def test_single_image(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("a cat"))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            result = await client.chat_image(
                "gpt-4o", "describe this", image_data=b"\x89PNG", media_type="image/png"
            )
        assert result.content == "a cat"
        request = httpx_mock.get_request()
        body = json.loads(request.content)
        msg = body["messages"][0]
        assert msg["content"][0]["type"] == "text"
        assert msg["content"][1]["type"] == "image_url"

    async def test_multiple_images(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("two images"))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            result = await client.chat_images(
                "gpt-4o",
                "compare",
                images=[(b"\x89PNG", "image/png"), (b"\xff\xd8", "image/jpeg")],
            )
        assert result.content == "two images"
        request = httpx_mock.get_request()
        body = json.loads(request.content)
        content_parts = body["messages"][0]["content"]
        assert len(content_parts) == 3  # text + 2 images


class TestContextManager:
    async def test_async_context_manager(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("ok"))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            result = await client.chat("gpt-4o", [{"role": "user", "content": "hi"}])
        assert result.content == "ok"
