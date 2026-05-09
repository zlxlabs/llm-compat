"""Tests for sensitive word pre-detection in LLMClient."""
from __future__ import annotations

from pytest_httpx import HTTPXMock

from llm_compat.client import LLMClient
from llm_compat.sensitive import SensitiveDetector


def _chat_response(content: str) -> dict:
    return {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


MESSAGES_CLEAN = [{"role": "user", "content": "正常的问题"}]
MESSAGES_SENSITIVE = [{"role": "user", "content": "包含敏感词的问题"}]


class TestSensitivePrescan:
    async def test_sensitive_detected_skips_primary(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_chat_response("fallback answer"))
        detector = SensitiveDetector(words=["敏感词"])
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
            sensitive_detector=detector,
        ) as client:
            result = await client.chat("deepseek-v4", MESSAGES_SENSITIVE)
            assert result.content == "fallback answer"
            assert result.model == "gpt-4.1-mini"
            assert result.fallback_from == "deepseek-v4"
            assert client.stats.prescan_skips == 1
            assert client.stats.fallback_count == 0

    async def test_clean_input_uses_primary(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_chat_response("primary answer"))
        detector = SensitiveDetector(words=["敏感词"])
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
            sensitive_detector=detector,
        ) as client:
            result = await client.chat("deepseek-v4", MESSAGES_CLEAN)
            assert result.content == "primary answer"
            assert result.fallback_from is None
            assert client.stats.prescan_skips == 0

    async def test_sensitive_no_fallback_config_uses_primary(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_chat_response("primary answer"))
        detector = SensitiveDetector(words=["敏感词"])
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            sensitive_detector=detector,
        ) as client:
            result = await client.chat("deepseek-v4", MESSAGES_SENSITIVE)
            assert result.content == "primary answer"
            assert result.fallback_from is None
