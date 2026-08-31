"""Collector reporting coverage, call-scoped evidence, and INFO-log hygiene."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import BaseModel
from pytest_httpx import HTTPXMock

from llm_compat._collector import CollectorClient
from llm_compat.client import LLMClient
from llm_compat.errors import ContentPolicyError, FatalError, JSONParseError
from llm_compat.sync import SyncLLMClient


class TagResult(BaseModel):
    tags: list[str]


SECRET_PROMPT = "SECRET_PROMPT_BODY_9f3a_do_not_log"
SECRET_RESPONSE = "SECRET_RESPONSE_BODY_9f3a_do_not_log"
MESSAGES = [{"role": "user", "content": SECRET_PROMPT}]
SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "llm_compat"


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


def _content_filter_response(content: str = SECRET_RESPONSE) -> dict:
    return _chat_response(content, finish_reason="content_filter")


def _refusal_text_response(content: str = "我无法回答这个问题") -> dict:
    return _chat_response(content)


class RecordingCollectorHttp:
    """Captures the JSON body CollectorClient actually POSTs (cross-process payload)."""

    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []
        self.order: list[str] = []

    async def post(self, url: str, json: dict[str, Any] | None = None) -> SimpleNamespace:
        self.order.append("collector")
        assert json is not None
        self.posts.append({"url": url, "json": json})
        return SimpleNamespace(status_code=200)


def _attach_recording_collector(client: LLMClient) -> RecordingCollectorHttp:
    http = RecordingCollectorHttp()
    client._collector = CollectorClient(
        url="http://collector.test",
        project="acctest",
        http=http,
    )
    return http


def _assert_payload_fields(body: dict[str, Any], *, model: str, layer: str) -> None:
    assert body["model"] == model
    assert body["detection_layer"] == layer
    assert "provider" in body
    assert "finish_reason" in body
    evidence = body["evidence"]
    assert isinstance(evidence, dict)
    assert evidence["layer"] == layer
    assert evidence["is_refusal"] is True


# --- source-level lock: no instance-scoped pending report ------------------


class TestNoSharedRefusalState:
    def test_pending_refusal_report_absent_from_src(self) -> None:
        hits: list[str] = []
        for path in SRC_ROOT.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "_pending_refusal_report" in text:
                hits.append(str(path.relative_to(SRC_ROOT.parent.parent)))
        assert hits == [], f"cross-call field still present: {hits}"

    def test_base_client_has_no_pending_refusal_attribute(self) -> None:
        client = LLMClient(base_url="https://api.test.com/v1", api_key="sk-test")
        assert not hasattr(client, "_pending_refusal_report")


# --- #26: report before raise, chat_json + chat symmetric ------------------


class TestRefusalReportingPaths:
    @pytest.mark.parametrize("method", ["chat", "chat_json"])
    async def test_all_refused_reports_before_raise(
        self, httpx_mock: HTTPXMock, method: str
    ) -> None:
        httpx_mock.add_response(json=_content_filter_response("filtered-a"))
        httpx_mock.add_response(json=_content_filter_response("filtered-b"))
        order: list[str] = []
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
        ) as client:
            recorder = _attach_recording_collector(client)
            recorder.order = order
            with pytest.raises(ContentPolicyError) as exc_info:
                if method == "chat":
                    await client.chat("deepseek-v4", MESSAGES)
                else:
                    await client.chat_json("deepseek-v4", MESSAGES, schema=TagResult)
                order.append("should_not_reach")
            order.append("exception")

        collector_at = [i for i, mark in enumerate(order) if mark == "collector"]
        exception_at = order.index("exception")
        assert collector_at, "expected collector reports before exception"
        assert collector_at[-1] < exception_at
        assert len(recorder.posts) == 2
        bodies = [item["json"] for item in recorder.posts]
        models = {body["model"] for body in bodies}
        assert models == {"deepseek-v4", "gpt-4.1-mini"}
        for body in bodies:
            _assert_payload_fields(body, model=body["model"], layer="structured_signal")
            assert body["finish_reason"] == "content_filter"
            assert body["evidence"]["signal"]
            assert "/refusals" in recorder.posts[0]["url"]
        assert exc_info.value.evidence is not None
        assert exc_info.value.attempt_layers == {
            "deepseek-v4": "structured_signal",
            "gpt-4.1-mini": "structured_signal",
        }

    async def test_chat_json_parse_failure_does_not_report(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(json=_chat_response("not-json"))
        async with LLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            recorder = _attach_recording_collector(client)
            with pytest.raises(JSONParseError):
                await client.chat_json("gpt-4o", MESSAGES, schema=TagResult)
        assert recorder.posts == []

    async def test_success_without_refusal_does_not_report(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(json=_chat_response("ok"))
        async with LLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            recorder = _attach_recording_collector(client)
            await client.chat("gpt-4o", MESSAGES)
        assert recorder.posts == []

    async def test_intermediate_refusals_reported_on_fallback_success(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(json=_content_filter_response("filtered"))
        httpx_mock.add_response(json=_chat_response('{"tags": ["ok"]}'))
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4o"]},
        ) as client:
            recorder = _attach_recording_collector(client)
            result = await client.chat_json("deepseek-v4", MESSAGES, schema=TagResult)
            assert result.parsed.tags == ["ok"]
        assert len(recorder.posts) == 1
        body = recorder.posts[0]["json"]
        _assert_payload_fields(body, model="deepseek-v4", layer="structured_signal")
        assert body["fallback_model"] == "gpt-4o"
        assert body["fallback_chain"] == ["deepseek-v4"]

    async def test_http_content_policy_reports_http_error_layer(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(
            status_code=400,
            json={"error": {"message": "content_policy_violation"}},
        )
        async with LLMClient(
            base_url="https://api.test.com/v1", api_key="sk-test", max_retries=0
        ) as client:
            recorder = _attach_recording_collector(client)
            with pytest.raises(ContentPolicyError):
                await client.chat("gpt-4o", MESSAGES)
        assert len(recorder.posts) == 1
        body = recorder.posts[0]["json"]
        _assert_payload_fields(body, model="gpt-4o", layer="http_error")
        assert body["http_status"] == 400
        assert body["finish_reason"] is None

    async def test_http_fatal_does_not_report(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(status_code=400, json={"error": "invalid request format"})
        async with LLMClient(
            base_url="https://api.test.com/v1", api_key="sk-test", max_retries=0
        ) as client:
            recorder = _attach_recording_collector(client)
            with pytest.raises(FatalError):
                await client.chat("gpt-4o", MESSAGES)
        assert recorder.posts == []

    async def test_no_collector_preserves_evidence_and_does_not_raise_extra(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(json=_content_filter_response())
        httpx_mock.add_response(json=_content_filter_response())
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
        ) as client:
            assert client._collector is None
            with pytest.raises(ContentPolicyError) as exc_info:
                await client.chat_json("deepseek-v4", MESSAGES, schema=TagResult)
        err = exc_info.value
        assert err.evidence is not None
        assert err.evidence.layer == "structured_signal"
        assert err.attempt_layers == {
            "deepseek-v4": "structured_signal",
            "gpt-4.1-mini": "structured_signal",
        }

    async def test_chat_image_refusal_reports_before_raise(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(json=_content_filter_response())
        order: list[str] = []
        async with LLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            recorder = _attach_recording_collector(client)
            recorder.order = order
            with pytest.raises(ContentPolicyError):
                await client.chat_image(
                    "gpt-4o", SECRET_PROMPT, image_data=b"abc", media_type="image/png",
                )
            order.append("exception")
        assert order.index("collector") < order.index("exception")
        _assert_payload_fields(
            recorder.posts[0]["json"], model="gpt-4o", layer="structured_signal"
        )


# --- #27: concurrent calls must not swap evidence --------------------------


class TestConcurrentRefusalIsolation:
    async def test_concurrent_reports_match_own_model_and_layer(
        self, httpx_mock: HTTPXMock
    ) -> None:
        expected: dict[str, str] = {
            "gpt-4o-slot-0": "structured_signal",
            "gpt-4o-slot-1": "http_error",
            "gpt-4o-slot-2": "structured_signal",
            "gpt-4o-slot-3": "http_error",
        }

        def callback(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            model = payload["model"]
            layer = expected[model]
            if layer == "http_error":
                return httpx.Response(
                    400,
                    json={"error": {"message": "content_policy_violation"}},
                )
            return httpx.Response(200, json=_content_filter_response(f"refused-{model}"))

        httpx_mock.add_callback(callback, is_reusable=True)

        async with LLMClient(
            base_url="https://api.test.com/v1", api_key="sk-test", max_retries=0
        ) as client:
            recorder = _attach_recording_collector(client)
            real_request = client._request

            async def delayed_request(payload: dict[str, Any]) -> dict[str, Any]:
                await asyncio.sleep(0.02)
                return await real_request(payload)

            client._request = delayed_request  # type: ignore[method-assign]

            async def one(model: str) -> None:
                with pytest.raises(ContentPolicyError):
                    await client.chat(
                        model,
                        [{"role": "user", "content": f"prompt-for-{model}"}],
                    )

            await asyncio.gather(*[one(model) for model in expected])

        by_model = {item["json"]["model"]: item["json"] for item in recorder.posts}
        assert set(by_model) == set(expected)
        for model, layer in expected.items():
            body = by_model[model]
            _assert_payload_fields(body, model=model, layer=layer)
            assert f"prompt-for-{model}" in body["input_preview"]
            if layer == "structured_signal":
                assert body["finish_reason"] == "content_filter"
                assert f"refused-{model}" in body["response_preview"]
            else:
                assert body["http_status"] == 400


# --- chat_stream: no collector reports -------------------------------------


class TestChatStreamReporting:
    async def test_stream_success_does_not_report(self, httpx_mock: HTTPXMock) -> None:
        sse = (
            b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
            b"data: [DONE]\n\n"
        )
        httpx_mock.add_response(
            stream=httpx.ByteStream(sse),
            headers={"content-type": "text/event-stream"},
        )
        async with LLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            recorder = _attach_recording_collector(client)
            async for _ in client.chat_stream("gpt-4o", MESSAGES):
                pass
        assert recorder.posts == []

    async def test_stream_http_error_does_not_report(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(status_code=500, json={"error": "upstream"})
        async with LLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            recorder = _attach_recording_collector(client)
            with pytest.raises(httpx.HTTPStatusError):
                async for _ in client.chat_stream("gpt-4o", MESSAGES):
                    pass
        assert recorder.posts == []


# --- SyncLLMClient: collector is async; reporting is an existing boundary --


class TestSyncDoesNotReport:
    def test_sync_refusal_does_not_call_collector(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_content_filter_response())
        with SyncLLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            client._collector = CollectorClient(url="http://collector.test")
            with pytest.raises(ContentPolicyError) as exc_info:
                client.chat("gpt-4o", MESSAGES)
            assert exc_info.value.evidence is not None
            assert not hasattr(client, "_pending_refusal_report")


# --- INFO logs must not contain prompt/response bodies ---------------------


class TestInfoLogHygiene:
    async def test_info_logs_omit_prompt_and_response_bodies(
        self, httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        httpx_mock.add_response(json=_content_filter_response())
        httpx_mock.add_response(json=_content_filter_response())
        with caplog.at_level("INFO"):
            async with LLMClient(
                base_url="https://api.test.com/v1",
                api_key="sk-test",
                content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
            ) as client:
                recorder = _attach_recording_collector(client)
                with pytest.raises(ContentPolicyError):
                    await client.chat_json("deepseek-v4", MESSAGES, schema=TagResult)
        info_text = "\n".join(
            record.getMessage() for record in caplog.records if record.levelname == "INFO"
        )
        assert SECRET_PROMPT not in info_text
        assert SECRET_RESPONSE not in info_text
        assert recorder.posts  # reporting still happened
        assert SECRET_PROMPT in recorder.posts[0]["json"]["input_preview"]
