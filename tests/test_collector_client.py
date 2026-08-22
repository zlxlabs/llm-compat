from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from llm_compat._collector import CollectorClient
from llm_compat.refusal import RefusalEvidence


@pytest.fixture
def mock_http() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def tmp_cache(tmp_path: Path) -> Path:
    return tmp_path / "cache.jsonl"


class TestReportRefusal:
    @pytest.mark.asyncio
    async def test_report_success(self, mock_http: AsyncMock, tmp_cache: Path):
        mock_http.post.return_value = AsyncMock(status_code=201)
        client = CollectorClient(
            url="http://localhost:8234",
            project="test-project",
            cache_path=tmp_cache,
            http=mock_http,
        )
        await client.report_refusal(
            model="deepseek-v4",
            provider="deepseek",
            detection_layer="http_error",
            http_status=500,
            input_text="这是测试内容，前200字会被截取",
            response_preview="sensitive_words_detected",
            evidence=RefusalEvidence(
                True, "structured_signal", signal="finish_reason=content_filter"
            ).to_dict(),
        )
        mock_http.post.assert_called_once()
        call_kwargs = mock_http.post.call_args
        body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert body["model"] == "deepseek-v4"
        assert body["source_project"] == "test-project"
        assert body["detection_layer"] == "http_error"
        assert body["http_status"] == 500
        assert body["evidence"]["layer"] == "structured_signal"

    @pytest.mark.asyncio
    async def test_report_falls_back_to_cache(self, mock_http: AsyncMock, tmp_cache: Path):
        mock_http.post.side_effect = Exception("connection refused")
        client = CollectorClient(
            url="http://localhost:8234",
            project="test-project",
            cache_path=tmp_cache,
            http=mock_http,
        )
        await client.report_refusal(
            model="deepseek-v4",
            detection_layer="structured_signal",
            input_text="test",
        )
        assert tmp_cache.exists()
        lines = tmp_cache.read_text().strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["model"] == "deepseek-v4"


class TestFetchWords:
    @pytest.mark.asyncio
    async def test_fetch_words_success(self, mock_http: AsyncMock, tmp_cache: Path):
        from unittest.mock import MagicMock

        resp_mock = MagicMock()
        resp_mock.status_code = 200
        resp_mock.json.return_value = {"words": ["词1", "词2"], "hash": "abc", "count": 2}
        mock_http.get.return_value = resp_mock

        client = CollectorClient(
            url="http://localhost:8234",
            cache_path=tmp_cache,
            http=mock_http,
        )
        words = await client.fetch_words()
        assert words == ["词1", "词2"]

    @pytest.mark.asyncio
    async def test_fetch_then_check_update_detects_change(
        self, mock_http: AsyncMock, tmp_cache: Path,
    ):
        from unittest.mock import MagicMock

        resp1 = MagicMock()
        resp1.json.return_value = {"words": ["词1"], "hash": "aaa", "count": 1}
        mock_http.get.return_value = resp1

        client = CollectorClient(url="http://localhost:8234", cache_path=tmp_cache, http=mock_http)
        await client.fetch_words()

        resp2 = MagicMock()
        resp2.json.return_value = {"hash": "bbb"}
        mock_http.get.return_value = resp2
        assert await client.check_update() is True

        resp3 = MagicMock()
        resp3.json.return_value = {"hash": "bbb"}
        mock_http.get.return_value = resp3
        assert await client.check_update() is False

    @pytest.mark.asyncio
    async def test_fetch_words_failure_returns_empty(self, mock_http: AsyncMock, tmp_cache: Path):
        mock_http.get.side_effect = Exception("connection refused")
        client = CollectorClient(
            url="http://localhost:8234",
            cache_path=tmp_cache,
            http=mock_http,
        )
        words = await client.fetch_words()
        assert words == []


class TestExtractPreview:
    def test_extract_text_messages(self):
        client = CollectorClient(url="http://localhost:8234", preview_length=20)
        messages = [
            {"role": "user", "content": "这是一段很长的测试文本内容"},
        ]
        preview = client.extract_preview(messages)
        assert len(preview) <= 20
        assert preview.startswith("这是一段")

    def test_extract_multimodal_messages(self):
        client = CollectorClient(url="http://localhost:8234", preview_length=100)
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "描述这张图"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ]},
        ]
        preview = client.extract_preview(messages)
        assert "描述这张图" in preview
