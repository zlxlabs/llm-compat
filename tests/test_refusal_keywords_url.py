from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llm_compat import LLMClient


class TestRefusalKeywordsUrl:
    @pytest.mark.asyncio
    async def test_loads_keywords_from_url(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "words": ["自定义拒绝词1", "自定义拒绝词2"], "hash": "a", "count": 2,
        }

        with patch("llm_compat._base.httpx.Client") as mock_client_cls:
            mock_http = MagicMock()
            mock_http.get.return_value = mock_resp
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_http)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

            client = LLMClient(
                base_url="http://api.example.com",
                api_key="sk-test",
                refusal_keywords_url="http://collector:8000/words",
            )
            assert "自定义拒绝词1" in client._refusal_keywords
            assert "自定义拒绝词2" in client._refusal_keywords

    @pytest.mark.asyncio
    async def test_url_failure_uses_empty(self):
        with patch("llm_compat._base.httpx.Client") as mock_client_cls:
            mock_http = MagicMock()
            mock_http.get.side_effect = Exception("connection refused")
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_http)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

            client = LLMClient(
                base_url="http://api.example.com",
                api_key="sk-test",
                refusal_keywords_url="http://collector:8000/words",
            )
            assert client._refusal_keywords is None or client._refusal_keywords == []

    @pytest.mark.asyncio
    async def test_url_merges_with_manual_keywords(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"words": ["url词"], "hash": "a", "count": 1}

        with patch("llm_compat._base.httpx.Client") as mock_client_cls:
            mock_http = MagicMock()
            mock_http.get.return_value = mock_resp
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_http)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

            client = LLMClient(
                base_url="http://api.example.com",
                api_key="sk-test",
                refusal_keywords=["手动词"],
                refusal_keywords_url="http://collector:8000/words",
            )
            assert "手动词" in client._refusal_keywords
            assert "url词" in client._refusal_keywords

    @pytest.mark.asyncio
    async def test_no_url_no_change(self):
        client = LLMClient(
            base_url="http://api.example.com",
            api_key="sk-test",
            refusal_keywords=["手动词"],
        )
        assert client._refusal_keywords == ["手动词"]
