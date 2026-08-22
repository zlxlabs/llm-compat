"""Tests for content fallback integration in LLMClient."""
from __future__ import annotations

import json

import pytest
from pytest_httpx import HTTPXMock

from llm_compat.client import LLMClient
from llm_compat.errors import ContentPolicyError
from llm_compat.providers import describe_from_payload


def _chat_response(content: str, *, finish_reason: str = "stop") -> dict:
    return {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def _refusal_response(content: str = "我无法回答该问题") -> dict:
    return _chat_response(content)


def _content_filter_response() -> dict:
    return {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": ""},
                "finish_reason": "content_filter",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10},
    }


MESSAGES = [{"role": "user", "content": "hello"}]


class TestFallbackBasic:
    async def test_no_fallback_config_normal_behavior(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_chat_response("hello world"))
        async with LLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            result = await client.chat("gpt-4o", MESSAGES)
            assert result.content == "hello world"
            assert result.fallback_from is None
            assert result.fallback_chain == []

    async def test_primary_refused_fallback_succeeds(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_refusal_response())
        httpx_mock.add_response(json=_chat_response("actual answer"))
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
        ) as client:
            result = await client.chat("deepseek-v4", MESSAGES)
            assert result.content == "actual answer"
            assert result.fallback_from == "deepseek-v4"
            assert result.model == "gpt-4.1-mini"
            assert "deepseek-v4" in result.fallback_chain

    async def test_fallback_keeps_extra_body_thinking_nested_for_gemini(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(json=_refusal_response())
        httpx_mock.add_response(json=_chat_response("fallback answer"))
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gemini-2.5-flash"]},
        ) as client:
            await client.chat(
                "deepseek-v4-flash",
                MESSAGES,
                extra_body={"thinking": {"type": "disabled"}},
            )

        requests = httpx_mock.get_requests()
        primary_body = json.loads(requests[0].content)
        fallback_body = json.loads(requests[1].content)
        assert "thinking" not in primary_body
        assert primary_body["extra_body"] == {"thinking": {"type": "disabled"}}
        assert fallback_body["model"] == "gemini-2.5-flash"
        assert "thinking" not in fallback_body
        assert fallback_body["extra_body"] == {"thinking": {"type": "disabled"}}
        assert describe_from_payload(fallback_body)["thinking_mode"] != "disabled"

    async def test_fallback_drops_direct_extra_thinking_for_gemini(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(json=_refusal_response())
        httpx_mock.add_response(json=_chat_response("fallback answer"))
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gemini-2.5-flash"]},
        ) as client:
            await client.chat(
                "deepseek-v4-flash",
                MESSAGES,
                thinking={"type": "disabled"},
            )

        requests = httpx_mock.get_requests()
        primary_body = json.loads(requests[0].content)
        fallback_body = json.loads(requests[1].content)
        assert "thinking" not in primary_body
        assert fallback_body["model"] == "gemini-2.5-flash"
        assert "thinking" not in fallback_body
        assert describe_from_payload(fallback_body)["thinking_source"] == "model_default"

    async def test_structured_signal_triggers_fallback(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_content_filter_response())
        httpx_mock.add_response(json=_chat_response("actual answer"))
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
        ) as client:
            result = await client.chat("deepseek-v4", MESSAGES)
            assert result.content == "actual answer"
            assert result.fallback_from == "deepseek-v4"

    async def test_all_models_refused_returns_longest_inferred_candidate(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(json=_refusal_response())
        httpx_mock.add_response(json=_refusal_response("I cannot assist"))
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
        ) as client:
            result = await client.chat("deepseek-v4", MESSAGES)
            assert result.content == "I cannot assist"
            assert result.refusal_suspected is True
            assert result.refusal_evidence is not None
            assert result.refusal_evidence.layer == "text_pattern"
            assert result.fallback_from == "deepseek-v4"

    async def test_all_models_refused_raise_mode_includes_layers(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(json=_refusal_response())
        httpx_mock.add_response(json=_refusal_response("I cannot assist"))
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
            on_all_refused="raise",
        ) as client:
            with pytest.raises(ContentPolicyError) as exc_info:
                await client.chat("deepseek-v4", MESSAGES)
        error = exc_info.value
        assert error.attempted_models == ["deepseek-v4", "gpt-4.1-mini"]
        assert error.attempt_layers == {
            "deepseek-v4": "text_pattern",
            "gpt-4.1-mini": "text_pattern",
        }
        assert "deepseek-v4=text_pattern" in str(error)
        assert "gpt-4.1-mini=text_pattern" in str(error)

    async def test_structured_refusals_are_never_rescued(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(json=_content_filter_response())
        httpx_mock.add_response(json=_content_filter_response())
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
        ) as client:
            with pytest.raises(ContentPolicyError) as exc_info:
                await client.chat("deepseek-v4", MESSAGES)
        assert exc_info.value.attempt_layers == {
            "deepseek-v4": "structured_signal",
            "gpt-4.1-mini": "structured_signal",
        }

    async def test_mixed_refusals_rescue_only_inferred_candidate(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(json=_content_filter_response())
        httpx_mock.add_response(json=_refusal_response("I cannot assist with this"))
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
        ) as client:
            result = await client.chat("deepseek-v4", MESSAGES)
        assert result.content == "I cannot assist with this"
        assert result.refusal_evidence is not None
        assert result.refusal_evidence.layer == "text_pattern"

    async def test_http_400_content_policy_triggers_fallback(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            status_code=400,
            json={"error": {"message": "content_policy_violation"}},
        )
        httpx_mock.add_response(json=_chat_response("fallback answer"))
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
            max_retries=0,
        ) as client:
            result = await client.chat("deepseek-v4", MESSAGES)
            assert result.content == "fallback answer"
            assert result.fallback_from == "deepseek-v4"

    async def test_model_not_in_fallback_config_no_fallback(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_refusal_response())
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
        ) as client:
            result = await client.chat("gpt-4o", MESSAGES)
            assert result.content == "我无法回答该问题"
            assert result.fallback_from is None


class TestFallbackChain:
    async def test_multi_fallback_chain(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_refusal_response())
        httpx_mock.add_response(json=_refusal_response("I cannot assist"))
        httpx_mock.add_response(json=_chat_response("third model works"))
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini", "gemini-2.5-flash"]},
        ) as client:
            result = await client.chat("deepseek-v4", MESSAGES)
            assert result.content == "third model works"
            assert result.model == "gemini-2.5-flash"
            assert len(result.fallback_chain) == 2


class TestFallbackStats:
    async def test_fallback_stats_recorded(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_refusal_response())
        httpx_mock.add_response(json=_chat_response("ok"))
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
        ) as client:
            await client.chat("deepseek-v4", MESSAGES)
            assert client.stats.fallback_count == 1
            assert client.stats._refusal_counts.get("deepseek-v4") == 1

    async def test_no_fallback_no_stats(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_chat_response("ok"))
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
        ) as client:
            await client.chat("deepseek-v4", MESSAGES)
            assert client.stats.fallback_count == 0


class TestCustomDetector:
    async def test_custom_detector_triggers_fallback(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_chat_response("short"))
        httpx_mock.add_response(json=_chat_response("a proper long answer"))

        from llm_compat.refusal import RefusalContext

        def short_detector(ctx: RefusalContext) -> bool:
            return len(ctx.content) < 10

        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
            refusal_detector=short_detector,
        ) as client:
            result = await client.chat("deepseek-v4", MESSAGES)
            assert result.content == "a proper long answer"
            assert result.fallback_from == "deepseek-v4"

    async def test_broken_detector_does_not_crash(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_chat_response("hello world"))

        from llm_compat.refusal import RefusalContext

        def broken(ctx: RefusalContext) -> bool:
            raise RuntimeError("boom")

        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
            refusal_detector=broken,
        ) as client:
            result = await client.chat("deepseek-v4", MESSAGES)
            assert result.content == "hello world"
