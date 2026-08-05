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

    async def test_chat_extra_body_is_forwarded_as_wire_field(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("ok"))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            await client.chat(
                "gpt-4o",
                [{"role": "user", "content": "hi"}],
                extra_body={"foo": "bar"},
            )
        body = json.loads(httpx_mock.get_request().content)
        assert body["extra_body"] == {"foo": "bar"}
        assert "foo" not in body

    async def test_chat_gemini_extra_body_is_forwarded_as_wire_field(
        self, httpx_mock: HTTPXMock
    ) -> None:
        extra_body = {
            "google": {
                "thinking_config": {"thinking_level": "low", "include_thoughts": True}
            }
        }
        httpx_mock.add_response(json=_chat_response("ok"))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            await client.chat(
                "gemini-3-flash",
                [{"role": "user", "content": "hi"}],
                extra_body=extra_body,
            )
        body = json.loads(httpx_mock.get_request().content)
        assert body["extra_body"] == extra_body

    async def test_chat_extra_body_thinking_is_nested_without_warning(
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
        assert body["extra_body"] == {"thinking": {"type": "disabled"}}
        assert "thinking" not in body
        assert not caplog.records

    async def test_chat_extra_body_does_not_override_provider_thinking(
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
        assert body["extra_body"] == {"thinking": {"type": "enabled"}}
        assert not caplog.records

    @pytest.mark.parametrize(
        "model",
        ["deepseek-v4-flash", "gemini-2.5-flash"],
    )
    async def test_chat_direct_extra_thinking_is_dropped_from_wire(
        self, httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture, model: str
    ) -> None:
        httpx_mock.add_response(json=_chat_response("ok"))
        with caplog.at_level("WARNING"):
            async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
                await client.chat(
                    model,
                    [{"role": "user", "content": "hi"}],
                    thinking={"type": "disabled"},
                )
        body = json.loads(httpx_mock.get_request().content)
        assert "thinking" not in body
        assert describe_from_payload(body)["thinking_source"] == "model_default"
        assert any(
            record.getMessage()
            == (
                "extra attempted to override reserved request field thinking; "
                "dropping value. Use reasoning_effort to control thinking."
            )
            for record in caplog.records
        )

    @pytest.mark.parametrize(
        ("model", "expected_field", "expected_value"),
        [
            ("deepseek-v4-flash", "thinking", {"type": "disabled"}),
            ("gemini-2.5-flash", "reasoning_effort", "none"),
        ],
    )
    async def test_named_reasoning_effort_wins_over_direct_extra_thinking(
        self,
        httpx_mock: HTTPXMock,
        model: str,
        expected_field: str,
        expected_value: object,
    ) -> None:
        httpx_mock.add_response(json=_chat_response("ok"))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            await client.chat(
                model,
                [{"role": "user", "content": "hi"}],
                reasoning_effort="disabled",
                thinking={"type": "enabled"},
            )
        body = json.loads(httpx_mock.get_request().content)
        assert body[expected_field] == expected_value
        assert "thinking" not in body or expected_field == "thinking"
        assert describe_from_payload(body)["thinking_mode"] == "disabled"

    async def test_chat_extra_body_reasoning_effort_does_not_override_translation(
        self, httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        httpx_mock.add_response(json=_chat_response("ok"))
        with caplog.at_level("WARNING"):
            async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
                await client.chat(
                    "gemini-2.5-flash",
                    [{"role": "user", "content": "hi"}],
                    reasoning_effort="disabled",
                    extra_body={"reasoning_effort": "high"},
                )
        body = json.loads(httpx_mock.get_request().content)
        assert body["reasoning_effort"] == "none"
        assert body["extra_body"] == {"reasoning_effort": "high"}
        assert describe_from_payload(body)["thinking_mode"] == "disabled"
        assert not caplog.records

    async def test_chat_direct_extra_stream_is_dropped(
        self, httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        httpx_mock.add_response(json=_chat_response("ok"))
        with caplog.at_level("WARNING"):
            async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
                await client.chat(
                    "gpt-4o",
                    [{"role": "user", "content": "hi"}],
                    stream=True,
                )
        body = json.loads(httpx_mock.get_request().content)
        assert "stream" not in body
        assert any(
            record.getMessage()
            == (
                "extra attempted to override reserved request field stream; "
                "dropping value. Use chat_stream to enable streaming."
            )
            for record in caplog.records
        )

    def test_build_payload_named_collisions_remain_type_errors(self) -> None:
        client = LLMClient(base_url="http://test/v1", api_key="sk-test")
        with pytest.raises(TypeError):
            client._build_payload("gpt-4o", [], "high", **{"reasoning_effort": "low"})
        with pytest.raises(TypeError):
            client._build_payload("gpt-4o", [], "high", **{"model": "other-model"})
        with pytest.raises(TypeError):
            client._build_payload("gpt-4o", [], "high", **{"messages": []})

    @pytest.mark.parametrize("extra_body", [None, [], "", "not-a-dict"])
    async def test_chat_extra_body_values_are_forwarded_without_validation(
        self,
        httpx_mock: HTTPXMock,
        caplog: pytest.LogCaptureFixture,
        extra_body: object,
    ) -> None:
        httpx_mock.add_response(json=_chat_response("ok"))
        with caplog.at_level("WARNING"):
            async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
                await client.chat(
                    "gpt-4o",
                    [{"role": "user", "content": "hi"}],
                    extra_body=extra_body,
                )
        body = json.loads(httpx_mock.get_request().content)
        assert body["extra_body"] == extra_body
        assert not caplog.records

    async def test_chat_nested_extra_body_is_preserved(
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
        assert body["extra_body"] == {"extra_body": {"foo": "bar"}}
        assert "foo" not in body
        assert not caplog.records

    @pytest.mark.parametrize(
        ("reserved_key", "reserved_value"),
        [
            ("model", "overridden-model"),
            ("messages", [{"role": "assistant", "content": "overridden"}]),
            ("stream", True),
            ("extra_body", {"foo": "bar"}),
            ("thinking", {"type": "disabled"}),
            ("reasoning_effort", "high"),
        ],
    )
    async def test_chat_extra_body_reserved_keys_are_forwarded_as_nested_fields(
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
        assert body["extra_body"] == {reserved_key: reserved_value}
        assert not caplog.records

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

    async def test_json_extra_body_response_format_does_not_override_top_level(
        self, httpx_mock: HTTPXMock
    ) -> None:
        nested_response_format = {"type": "json_object"}
        httpx_mock.add_response(json=_chat_response('{"tags": ["python"]}'))
        async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            result = await client.chat_json(
                "gpt-5-mini",
                [{"role": "user", "content": "tag this"}],
                schema=TagResult,
                extra_body={"response_format": nested_response_format},
            )
            assert client.stats.json_schema_calls == 1
        assert result.parsed.tags == ["python"]
        body = json.loads(httpx_mock.get_request().content)
        assert body["response_format"]["type"] == "json_schema"
        assert body["extra_body"]["response_format"] == nested_response_format


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
        body = json.loads(httpx_mock.get_request().content)
        assert body["stream"] is True

    async def test_stream_named_argument_wins_over_direct_extra_stream(
        self, httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        sse_data = b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
        httpx_mock.add_response(
            stream=httpx.ByteStream(sse_data),
            headers={"content-type": "text/event-stream"},
        )
        with caplog.at_level("WARNING"):
            async with LLMClient(base_url="http://test/v1", api_key="sk-test") as client:
                chunks = [
                    chunk
                    async for chunk in client.chat_stream(
                        "gpt-4o",
                        [{"role": "user", "content": "hi"}],
                        stream=False,
                    )
                ]
        assert chunks == ["ok"]
        body = json.loads(httpx_mock.get_request().content)
        assert body["stream"] is True
        assert any(
            record.getMessage()
            == (
                "extra attempted to override reserved request field stream; "
                "dropping value. Use chat_stream to enable streaming."
            )
            for record in caplog.records
        )

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
