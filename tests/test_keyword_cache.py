from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llm_compat._keyword_cache import (
    _cache_version,
    _keyword_cache,
    _polling_urls,
    get_cache_version,
    get_cached_keywords,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    _keyword_cache.clear()
    _polling_urls.clear()
    _cache_version.clear()
    yield
    _keyword_cache.clear()
    _polling_urls.clear()
    _cache_version.clear()


def _mock_text_response(text: str) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.text = text
    return mock_resp


def _patch_httpx(mock_resp: MagicMock | None = None, side_effect: Exception | None = None):
    patcher = patch("llm_compat._keyword_cache.httpx.Client")
    mock_cls = patcher.start()
    mock_http = MagicMock()
    if side_effect:
        mock_http.get.side_effect = side_effect
    else:
        mock_http.get.return_value = mock_resp
    mock_cls.return_value.__enter__ = MagicMock(return_value=mock_http)
    mock_cls.return_value.__exit__ = MagicMock(return_value=False)
    return patcher


class TestPlainTextParsing:
    def test_basic_words(self):
        resp = _mock_text_response("词1\n词2\n词3\n")
        p = _patch_httpx(resp)
        try:
            result = get_cached_keywords("http://test:8000/words.txt")
            assert result == ["词1", "词2", "词3"]
        finally:
            p.stop()

    def test_comments_and_empty_lines(self):
        resp = _mock_text_response("# 这是注释\n词1\n\n# 另一个注释\n词2\n  \n词3\n")
        p = _patch_httpx(resp)
        try:
            result = get_cached_keywords("http://test:8000/words.txt")
            assert result == ["词1", "词2", "词3"]
        finally:
            p.stop()

    def test_strips_whitespace(self):
        resp = _mock_text_response("  词1  \n\t词2\t\n")
        p = _patch_httpx(resp)
        try:
            result = get_cached_keywords("http://test:8000/words.txt")
            assert result == ["词1", "词2"]
        finally:
            p.stop()

    def test_empty_response(self):
        resp = _mock_text_response("")
        p = _patch_httpx(resp)
        try:
            result = get_cached_keywords("http://test:8000/words.txt")
            assert result == []
        finally:
            p.stop()

    def test_only_comments(self):
        resp = _mock_text_response("# comment1\n# comment2\n")
        p = _patch_httpx(resp)
        try:
            result = get_cached_keywords("http://test:8000/words.txt")
            assert result == []
        finally:
            p.stop()


class TestCaching:
    def test_first_call_fetches_and_caches(self):
        resp = _mock_text_response("词1\n词2\n")
        p = _patch_httpx(resp)
        try:
            result = get_cached_keywords("http://test:8000/words.txt")
            assert "词1" in result
            assert "词2" in result
        finally:
            p.stop()

    def test_second_call_uses_cache_no_fetch(self):
        _keyword_cache["http://test:8000/words"] = ["cached"]
        result = get_cached_keywords("http://test:8000/words")
        assert result == ["cached"]

    def test_fetch_failure_returns_empty_on_cold_start(self):
        p = _patch_httpx(side_effect=Exception("connection refused"))
        try:
            result = get_cached_keywords("http://unreachable:8000/words")
            assert result == []
        finally:
            p.stop()

    def test_fetch_failure_returns_stale_cache(self):
        _keyword_cache["http://test:8000/words"] = ["旧词"]
        p = _patch_httpx(side_effect=Exception("timeout"))
        try:
            from llm_compat._keyword_cache import _refresh_url
            _refresh_url("http://test:8000/words")
        finally:
            p.stop()
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


class TestCacheVersion:
    def test_initial_version_is_zero(self):
        assert get_cache_version("http://test:8000/words") == 0

    def test_version_increments_on_refresh(self):
        resp = _mock_text_response("词1\n")
        p = _patch_httpx(resp)
        try:
            from llm_compat._keyword_cache import _refresh_url
            _refresh_url("http://test:8000/words")
            assert get_cache_version("http://test:8000/words") == 1
            _refresh_url("http://test:8000/words")
            assert get_cache_version("http://test:8000/words") == 2
        finally:
            p.stop()

    def test_version_unchanged_on_refresh_failure(self):
        _keyword_cache["http://test:8000/words"] = ["旧词"]
        _cache_version["http://test:8000/words"] = 1
        p = _patch_httpx(side_effect=Exception("timeout"))
        try:
            from llm_compat._keyword_cache import _refresh_url
            _refresh_url("http://test:8000/words")
        finally:
            p.stop()
        assert get_cache_version("http://test:8000/words") == 1

    def test_first_fetch_sets_version(self):
        resp = _mock_text_response("词1\n")
        p = _patch_httpx(resp)
        try:
            get_cached_keywords("http://test:8000/words.txt")
            assert get_cache_version("http://test:8000/words.txt") == 1
        finally:
            p.stop()
