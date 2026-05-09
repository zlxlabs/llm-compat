from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llm_compat._keyword_cache import _keyword_cache, _polling_urls, get_cached_keywords


@pytest.fixture(autouse=True)
def _clear_cache():
    _keyword_cache.clear()
    _polling_urls.clear()
    yield
    _keyword_cache.clear()
    _polling_urls.clear()


class TestGetCachedKeywords:
    def test_first_call_fetches_and_caches(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"words": ["词1", "词2"]}

        with patch("llm_compat._keyword_cache.httpx.Client") as mock_cls:
            mock_http = MagicMock()
            mock_http.get.return_value = mock_resp
            mock_cls.return_value.__enter__ = MagicMock(return_value=mock_http)
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)

            result = get_cached_keywords("http://test:8000/words")
            assert "词1" in result
            assert "词2" in result

    def test_second_call_uses_cache_no_fetch(self):
        _keyword_cache["http://test:8000/words"] = ["cached"]

        result = get_cached_keywords("http://test:8000/words")
        assert result == ["cached"]

    def test_fetch_failure_returns_empty_on_cold_start(self):
        with patch("llm_compat._keyword_cache.httpx.Client") as mock_cls:
            mock_http = MagicMock()
            mock_http.get.side_effect = Exception("connection refused")
            mock_cls.return_value.__enter__ = MagicMock(return_value=mock_http)
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)

            result = get_cached_keywords("http://unreachable:8000/words")
            assert result == []

    def test_fetch_failure_returns_stale_cache(self):
        _keyword_cache["http://test:8000/words"] = ["旧词"]

        with patch("llm_compat._keyword_cache.httpx.Client") as mock_cls:
            mock_http = MagicMock()
            mock_http.get.side_effect = Exception("timeout")
            mock_cls.return_value.__enter__ = MagicMock(return_value=mock_http)
            mock_cls.return_value.__exit__ = MagicMock(return_value=False)

            from llm_compat._keyword_cache import _refresh_url
            _refresh_url("http://test:8000/words")

        assert _keyword_cache["http://test:8000/words"] == ["旧词"]

    def test_registers_url_for_polling(self):
        _keyword_cache["http://test:8000/words"] = ["x"]
        get_cached_keywords("http://test:8000/words")
        assert "http://test:8000/words" in _polling_urls

    def test_multiple_urls_cached_independently(self):
        _keyword_cache["http://a:8000/words"] = ["词A"]
        _keyword_cache["http://b:8000/words"] = ["词B"]

        assert get_cached_keywords("http://a:8000/words") == ["词A"]
        assert get_cached_keywords("http://b:8000/words") == ["词B"]
