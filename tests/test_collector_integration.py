from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from llm_compat import LLMClient


def _make_ok_response(content: str = "ok") -> dict:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def _make_refusal_response() -> dict:
    return {
        "choices": [{"message": {"content": "我无法回答这个问题"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


class TestCollectorIntegration:
    @pytest.mark.asyncio
    async def test_collector_url_creates_collector_client(self):
        client = LLMClient(
            base_url="http://api.example.com",
            api_key="sk-test",
            collector_url="http://collector:8000",
            collector_project="test",
        )
        assert client._collector is not None
        assert client._collector._project == "test"

    @pytest.mark.asyncio
    async def test_no_collector_url_means_no_collector(self):
        client = LLMClient(
            base_url="http://api.example.com",
            api_key="sk-test",
        )
        assert client._collector is None

    @pytest.mark.asyncio
    async def test_fallback_triggers_collector_report(self):
        from unittest.mock import MagicMock

        from llm_compat._collector import CollectorClient

        mock_collector = MagicMock()
        mock_collector.report_refusal = AsyncMock()
        real_collector = CollectorClient(url="http://test:8000")
        mock_collector.extract_preview = real_collector.extract_preview

        client = LLMClient(
            base_url="http://api.example.com",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
        )
        client._collector = mock_collector

        call_count = 0

        async def mock_request(payload):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_refusal_response()
            return _make_ok_response("fallback ok")

        client._request = mock_request

        result = await client.chat(
            "deepseek-v4",
            [{"role": "user", "content": "测试内容"}],
        )
        assert result.fallback_from == "deepseek-v4"
        mock_collector.report_refusal.assert_called()
        call_kwargs = mock_collector.report_refusal.call_args.kwargs
        assert call_kwargs["model"] == "deepseek-v4"
        assert call_kwargs["detection_layer"] == "text_pattern"
        assert call_kwargs["evidence"]["layer"] == "text_pattern"
        assert call_kwargs["fallback_model"] == "gpt-4.1-mini"
        assert call_kwargs["message_count"] == 1
        assert "测试内容" in call_kwargs["input_text"]
        assert "我无法回答" in call_kwargs["response_preview"]

    @pytest.mark.asyncio
    async def test_no_fallback_no_report(self):
        mock_collector = AsyncMock()
        mock_collector.report_refusal = AsyncMock()

        client = LLMClient(
            base_url="http://api.example.com",
            api_key="sk-test",
        )
        client._collector = mock_collector

        async def mock_request(payload):
            return _make_ok_response("success")

        client._request = mock_request

        result = await client.chat(
            "gpt-4.1-mini",
            [{"role": "user", "content": "hello"}],
        )
        assert result.content == "success"
        mock_collector.report_refusal.assert_not_called()
