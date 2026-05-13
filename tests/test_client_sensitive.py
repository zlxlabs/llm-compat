"""Tests for sensitive word pre-detection in LLMClient."""
from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from llm_compat._keyword_cache import _cache_version, _keyword_cache, _polling_urls
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


@pytest.fixture(autouse=True)
def _clear_cache():
    _keyword_cache.clear()
    _polling_urls.clear()
    _cache_version.clear()
    yield
    _keyword_cache.clear()
    _polling_urls.clear()
    _cache_version.clear()


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


class TestSensitiveWordsUrl:
    def test_single_url_builds_detector(self):
        _keyword_cache["http://test:8000/words.txt"] = ["敏感词"]
        _cache_version["http://test:8000/words.txt"] = 1
        client = LLMClient(
            base_url="http://api.example.com",
            api_key="sk-test",
            sensitive_words_url="http://test:8000/words.txt",
        )
        detector = client._get_sensitive_detector()
        assert detector is not None
        assert detector.is_available
        assert detector.contains("包含敏感词的文字")

    def test_multiple_urls_merged(self):
        _keyword_cache["http://a:8000/words.txt"] = ["词A"]
        _keyword_cache["http://b:8000/words.txt"] = ["词B"]
        _cache_version["http://a:8000/words.txt"] = 1
        _cache_version["http://b:8000/words.txt"] = 1
        client = LLMClient(
            base_url="http://api.example.com",
            api_key="sk-test",
            sensitive_words_url=["http://a:8000/words.txt", "http://b:8000/words.txt"],
        )
        detector = client._get_sensitive_detector()
        assert detector is not None
        assert detector.contains("包含词A的文字")
        assert detector.contains("包含词B的文字")

    def test_url_plus_manual_detector_merged(self):
        _keyword_cache["http://test:8000/words.txt"] = ["URL词"]
        _cache_version["http://test:8000/words.txt"] = 1
        manual = SensitiveDetector(words=["手动词"])
        client = LLMClient(
            base_url="http://api.example.com",
            api_key="sk-test",
            sensitive_words_url="http://test:8000/words.txt",
            sensitive_detector=manual,
        )
        detector = client._get_sensitive_detector()
        assert detector is not None
        assert detector.contains("包含URL词的文字")
        assert detector.contains("包含手动词的文字")

    def test_url_failure_falls_back_to_manual(self):
        # URL cache is empty (simulates failure)
        manual = SensitiveDetector(words=["手动词"])
        client = LLMClient(
            base_url="http://api.example.com",
            api_key="sk-test",
            sensitive_words_url="http://unreachable:8000/words.txt",
            sensitive_detector=manual,
        )
        detector = client._get_sensitive_detector()
        assert detector is not None
        assert detector.contains("包含手动词的文字")

    def test_no_url_returns_manual_detector(self):
        manual = SensitiveDetector(words=["手动词"])
        client = LLMClient(
            base_url="http://api.example.com",
            api_key="sk-test",
            sensitive_detector=manual,
        )
        detector = client._get_sensitive_detector()
        assert detector is manual

    def test_no_url_no_detector_returns_none(self):
        client = LLMClient(
            base_url="http://api.example.com",
            api_key="sk-test",
        )
        assert client._get_sensitive_detector() is None


class TestSensitiveDetectorRebuild:
    def test_rebuild_on_version_change(self):
        _keyword_cache["http://test:8000/words.txt"] = ["词1"]
        _cache_version["http://test:8000/words.txt"] = 1
        client = LLMClient(
            base_url="http://api.example.com",
            api_key="sk-test",
            sensitive_words_url="http://test:8000/words.txt",
        )
        d1 = client._get_sensitive_detector()
        assert d1 is not None
        assert d1.contains("词1")
        assert not d1.contains("词2")

        _keyword_cache["http://test:8000/words.txt"] = ["词1", "词2"]
        _cache_version["http://test:8000/words.txt"] = 2

        d2 = client._get_sensitive_detector()
        assert d2 is not d1
        assert d2.contains("词2")

    def test_no_rebuild_when_version_unchanged(self):
        _keyword_cache["http://test:8000/words.txt"] = ["词1"]
        _cache_version["http://test:8000/words.txt"] = 1
        client = LLMClient(
            base_url="http://api.example.com",
            api_key="sk-test",
            sensitive_words_url="http://test:8000/words.txt",
        )
        d1 = client._get_sensitive_detector()
        d2 = client._get_sensitive_detector()
        assert d1 is d2

    async def test_url_detector_works_in_prescan(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json=_chat_response("fallback answer"))
        _keyword_cache["http://test:8000/words.txt"] = ["敏感词"]
        _cache_version["http://test:8000/words.txt"] = 1
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
            sensitive_words_url="http://test:8000/words.txt",
        ) as client:
            result = await client.chat("deepseek-v4", MESSAGES_SENSITIVE)
            assert result.model == "gpt-4.1-mini"
            assert client.stats.prescan_skips == 1
