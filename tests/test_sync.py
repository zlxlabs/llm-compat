"""Tests for llm_compat.sync — sync LLM client using real httpx.Client."""
from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel
from pytest_httpx import HTTPXMock

from llm_compat._types import ChatResult
from llm_compat.errors import JSONParseError
from llm_compat.sync import SyncLLMClient


class TagResult(BaseModel):
    tags: list[str]


def _chat_response(content: str) -> dict:
    return {
        "id": "chatcmpl-123",
        "model": "gpt-4o",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


class TestSyncChat:
    def test_basic_chat(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("hello"))
        with SyncLLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            result = client.chat("gpt-4o", [{"role": "user", "content": "hi"}])
        assert isinstance(result, ChatResult)
        assert result.content == "hello"
        assert result.usage is not None
        assert result.usage.total_tokens == 30

    def test_chat_with_reasoning_effort(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("deep thought"))
        with SyncLLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            result = client.chat(
                "deepseek-v4-flash", [{"role": "user", "content": "think"}],
                reasoning_effort="high",
            )
        assert result.content == "deep thought"
        request = httpx_mock.get_request()
        body = json.loads(request.content)
        assert body["reasoning_effort"] == "high"


class TestSyncChatJson:
    def test_json_with_schema(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response('{"tags": ["python"]}'))
        with SyncLLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            result = client.chat_json("gpt-4o", [], schema=TagResult)
        assert result.parsed.tags == ["python"]

    def test_json_parse_error(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("broken"))
        with SyncLLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            with pytest.raises(JSONParseError):
                client.chat_json("gpt-4o", [], schema=TagResult)


class TestSyncChatImage:
    def test_single_image(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("a cat"))
        with SyncLLMClient(base_url="http://test/v1", api_key="sk-test") as client:
            result = client.chat_image(
                "gpt-4o", "describe", image_data=b"\x89PNG", media_type="image/png"
            )
        assert result.content == "a cat"
