from __future__ import annotations

import pytest

from llm_compat import LLMClient
from llm_compat._keyword_cache import _keyword_cache, _polling_urls
from llm_compat.refusal import RefusalPolicy, detect_refusal


@pytest.fixture(autouse=True)
def _clear_cache():
    _keyword_cache.clear()
    _polling_urls.clear()
    yield
    _keyword_cache.clear()
    _polling_urls.clear()


class TestSingleUrl:
    def test_replace_policy_uses_url_words_without_builtin_patterns(self):
        data = {
            "choices": [{
                "message": {"content": "url refusal"},
                "finish_reason": "stop",
            }]
        }
        evidence = detect_refusal(
            data,
            policy=RefusalPolicy(keywords_mode="replace", extra_keywords=("url",)),
        )
        assert evidence.is_refusal is True
        assert evidence.signal == "keyword:url"

    @pytest.mark.parametrize(
        ("mode", "expected"),
        [("replace", False), ("extend", True)],
    )
    def test_builtin_pattern_depends_on_keyword_mode(self, mode, expected):
        data = {
            "choices": [{
                "message": {"content": "I cannot assist with that request"},
                "finish_reason": "stop",
            }]
        }
        evidence = detect_refusal(
            data,
            policy=RefusalPolicy(keywords_mode=mode, extra_keywords=()),
        )
        assert evidence.is_refusal is expected

    def test_loads_keywords_from_url(self):
        _keyword_cache["http://test:8000/words"] = ["url词1", "url词2"]
        client = LLMClient(
            base_url="http://api.example.com",
            api_key="sk-test",
            refusal_keywords_url="http://test:8000/words",
        )
        kw = client._get_refusal_keywords()
        assert "url词1" in kw
        assert "url词2" in kw

    def test_merges_with_manual_keywords(self):
        _keyword_cache["http://test:8000/words"] = ["url词"]
        client = LLMClient(
            base_url="http://api.example.com",
            api_key="sk-test",
            refusal_keywords=["手动词"],
            refusal_keywords_url="http://test:8000/words",
        )
        kw = client._get_refusal_keywords()
        assert "手动词" in kw
        assert "url词" in kw

    def test_no_url_no_change(self):
        client = LLMClient(
            base_url="http://api.example.com",
            api_key="sk-test",
            refusal_keywords=["手动词"],
        )
        kw = client._get_refusal_keywords()
        assert kw == ["手动词"]

    def test_no_url_no_manual_returns_none(self):
        client = LLMClient(
            base_url="http://api.example.com",
            api_key="sk-test",
        )
        assert client._get_refusal_keywords() is None


class TestMultipleUrls:
    def test_multiple_urls_merged(self):
        _keyword_cache["http://a:8000/words"] = ["词A"]
        _keyword_cache["http://b:8000/words"] = ["词B"]
        client = LLMClient(
            base_url="http://api.example.com",
            api_key="sk-test",
            refusal_keywords_url=[
                "http://a:8000/words",
                "http://b:8000/words",
            ],
        )
        kw = client._get_refusal_keywords()
        assert "词A" in kw
        assert "词B" in kw

    def test_deduplication(self):
        _keyword_cache["http://a:8000/words"] = ["重复词", "词A"]
        _keyword_cache["http://b:8000/words"] = ["重复词", "词B"]
        client = LLMClient(
            base_url="http://api.example.com",
            api_key="sk-test",
            refusal_keywords=["重复词"],
            refusal_keywords_url=[
                "http://a:8000/words",
                "http://b:8000/words",
            ],
        )
        kw = client._get_refusal_keywords()
        assert kw.count("重复词") == 1
        assert "词A" in kw
        assert "词B" in kw


class TestDynamicUpdate:
    def test_cache_update_reflects_in_keywords(self):
        _keyword_cache["http://test:8000/words"] = ["旧词"]
        client = LLMClient(
            base_url="http://api.example.com",
            api_key="sk-test",
            refusal_keywords_url="http://test:8000/words",
        )
        assert "旧词" in client._get_refusal_keywords()

        _keyword_cache["http://test:8000/words"] = ["旧词", "新词"]
        kw = client._get_refusal_keywords()
        assert "新词" in kw
        assert "旧词" in kw

    def test_partial_url_failure_keeps_others(self):
        _keyword_cache["http://a:8000/words"] = ["词A"]
        # URL B not cached (simulates failure)
        client = LLMClient(
            base_url="http://api.example.com",
            api_key="sk-test",
            refusal_keywords_url=[
                "http://a:8000/words",
                "http://b:8000/words",
            ],
        )
        kw = client._get_refusal_keywords()
        assert "词A" in kw
