"""Logical-call stats: one chat/chat_json/chat_image/stream invocation → one record."""
from __future__ import annotations

import httpx
import pytest
from pydantic import BaseModel
from pytest_httpx import HTTPXMock

from llm_compat.client import LLMClient
from llm_compat.errors import ContentPolicyError, FatalError, JSONParseError, RetryableError
from llm_compat.sync import SyncLLMClient


class TagResult(BaseModel):
    tags: list[str]


MESSAGES = [{"role": "user", "content": "hello"}]


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


def _content_filter_response(content: str = "provider partial output") -> dict:
    return _chat_response(content, finish_reason="content_filter")


def _refusal_text_response(content: str = "我无法回答这个问题") -> dict:
    return _chat_response(content)


def _assert_balanced(client: LLMClient | SyncLLMClient) -> None:
    assert client.stats.total_calls == client.stats.success_count + client.stats.error_count


# --- chat() -----------------------------------------------------------------


class TestChatAccounting:
    async def test_success_records_once(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("ok"))
        async with LLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            result = await client.chat("gpt-4o", MESSAGES)
            assert result.content == "ok"
            assert client.stats.success_count == 1
            assert client.stats.error_count == 0
            _assert_balanced(client)

    async def test_structured_refusal_records_error_once(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_content_filter_response())
        async with LLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            with pytest.raises(ContentPolicyError):
                await client.chat("gpt-4o", MESSAGES)
            assert client.stats.success_count == 0
            assert client.stats.error_count == 1
            _assert_balanced(client)

    async def test_http_fatal_records_error_once(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(status_code=400, json={"error": "bad request"})
        async with LLMClient(
            base_url="https://api.test.com/v1", api_key="sk-test", max_retries=0
        ) as client:
            with pytest.raises(FatalError):
                await client.chat("gpt-4o", MESSAGES)
            assert client.stats.success_count == 0
            assert client.stats.error_count == 1
            _assert_balanced(client)

    async def test_http_content_policy_records_error_once(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            status_code=400,
            json={"error": {"message": "content_policy_violation"}},
        )
        async with LLMClient(
            base_url="https://api.test.com/v1", api_key="sk-test", max_retries=0
        ) as client:
            with pytest.raises(ContentPolicyError):
                await client.chat("gpt-4o", MESSAGES)
            assert client.stats.success_count == 0
            assert client.stats.error_count == 1
            _assert_balanced(client)

    async def test_network_error_records_error_once(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_exception(httpx.ConnectError("boom"))
        async with LLMClient(
            base_url="https://api.test.com/v1", api_key="sk-test", max_retries=0
        ) as client:
            with pytest.raises(RetryableError):
                await client.chat("gpt-4o", MESSAGES)
            assert client.stats.success_count == 0
            assert client.stats.error_count == 1
            _assert_balanced(client)


# --- chat_json() without fallback ------------------------------------------


class TestChatJsonNoFallbackAccounting:
    async def test_success_records_once(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response('{"tags": ["a"]}'))
        async with LLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            result = await client.chat_json("gpt-4o", MESSAGES, schema=TagResult)
            assert result.parsed.tags == ["a"]
            assert client.stats.success_count == 1
            assert client.stats.error_count == 0
            _assert_balanced(client)

    async def test_parse_failure_records_error_not_success(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """#24: parse failure after a 200 must not also record success."""
        httpx_mock.add_response(json=_chat_response("not-valid-json"))
        async with LLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            with pytest.raises(JSONParseError):
                await client.chat_json("gpt-4o", MESSAGES, schema=TagResult)
            assert client.stats.success_count == 0
            assert client.stats.error_count == 1
            assert client.stats.total_calls == 1
            _assert_balanced(client)

    async def test_self_correction_exhausted_records_error_once(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(json=_chat_response("bad1"))
        httpx_mock.add_response(json=_chat_response("bad2"))
        httpx_mock.add_response(json=_chat_response("bad3"))
        async with LLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            with pytest.raises(JSONParseError):
                await client.chat_json(
                    "gpt-4o", MESSAGES, schema=TagResult,
                    self_correction=True, max_retries=2,
                )
            assert client.stats.success_count == 0
            assert client.stats.error_count == 1
            _assert_balanced(client)

    async def test_structured_refusal_records_error_once(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_content_filter_response())
        async with LLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            with pytest.raises(ContentPolicyError):
                await client.chat_json("gpt-4o", MESSAGES, schema=TagResult)
            assert client.stats.success_count == 0
            assert client.stats.error_count == 1
            _assert_balanced(client)

    async def test_http_fatal_records_error_once(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(status_code=400, json={"error": "bad request"})
        async with LLMClient(
            base_url="https://api.test.com/v1", api_key="sk-test", max_retries=0
        ) as client:
            with pytest.raises(FatalError):
                await client.chat_json("gpt-4o", MESSAGES, schema=TagResult)
            assert client.stats.success_count == 0
            assert client.stats.error_count == 1
            _assert_balanced(client)


# --- chat_json() with fallback chain ---------------------------------------


class TestChatJsonFallbackAccounting:
    async def test_rescue_success_records_success_once(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_refusal_text_response())
        httpx_mock.add_response(json=_refusal_text_response("I cannot assist with that."))
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
        ) as client:
            result = await client.chat("deepseek-v4", MESSAGES)
            assert result.refusal_suspected is True
            assert client.stats.success_count == 1
            assert client.stats.error_count == 0
            _assert_balanced(client)

    async def test_fallback_to_working_model_records_success_once(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(json=_content_filter_response())
        httpx_mock.add_response(json=_chat_response('{"tags": ["ok"]}'))
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4o"]},
        ) as client:
            result = await client.chat_json("deepseek-v4", MESSAGES, schema=TagResult)
            assert result.parsed.tags == ["ok"]
            assert client.stats.success_count == 1
            assert client.stats.error_count == 0
            _assert_balanced(client)

    async def test_json_rescue_parse_failure_records_error_once(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(json=_refusal_text_response())
        httpx_mock.add_response(json=_refusal_text_response("I cannot assist"))
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
        ) as client:
            with pytest.raises(ContentPolicyError):
                await client.chat_json("deepseek-v4", MESSAGES, schema=TagResult)
            assert client.stats.success_count == 0
            assert client.stats.error_count == 1
            _assert_balanced(client)

    async def test_all_refused_chain_records_error_once(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_content_filter_response())
        httpx_mock.add_response(json=_content_filter_response("also filtered"))
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
        ) as client:
            with pytest.raises(ContentPolicyError):
                await client.chat_json("deepseek-v4", MESSAGES, schema=TagResult)
            assert client.stats.success_count == 0
            assert client.stats.error_count == 1
            _assert_balanced(client)

    async def test_last_model_parse_failure_records_error_once(
        self, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(json=_content_filter_response())
        httpx_mock.add_response(json=_chat_response("not-json"))
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4o"]},
        ) as client:
            with pytest.raises(JSONParseError):
                await client.chat_json("deepseek-v4", MESSAGES, schema=TagResult)
            assert client.stats.success_count == 0
            assert client.stats.error_count == 1
            _assert_balanced(client)

    async def test_http_fatal_on_chain_records_error_once(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_content_filter_response())
        httpx_mock.add_response(status_code=400, json={"error": "bad request"})
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4o"]},
            max_retries=0,
        ) as client:
            with pytest.raises(FatalError):
                await client.chat_json("deepseek-v4", MESSAGES, schema=TagResult)
            assert client.stats.success_count == 0
            assert client.stats.error_count == 1
            _assert_balanced(client)


# --- chat_image / chat_images ----------------------------------------------


class TestChatImageAccounting:
    async def test_image_success_records_once(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("a cat"))
        async with LLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            result = await client.chat_image(
                "gpt-4o", "describe", image_data=b"abc", media_type="image/png",
            )
            assert result.content == "a cat"
            assert client.stats.success_count == 1
            assert client.stats.error_count == 0
            _assert_balanced(client)

    async def test_images_success_records_once(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("two cats"))
        async with LLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            result = await client.chat_images(
                "gpt-4o", "describe", images=[(b"abc", "image/png"), (b"def", "image/jpeg")],
            )
            assert result.content == "two cats"
            assert client.stats.success_count == 1
            _assert_balanced(client)

    async def test_image_refusal_records_error_once(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_content_filter_response())
        async with LLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            with pytest.raises(ContentPolicyError):
                await client.chat_image(
                    "gpt-4o", "describe", image_data=b"abc", media_type="image/png",
                )
            assert client.stats.success_count == 0
            assert client.stats.error_count == 1
            _assert_balanced(client)

    async def test_image_http_fatal_records_error_once(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(status_code=400, json={"error": "bad request"})
        async with LLMClient(
            base_url="https://api.test.com/v1", api_key="sk-test", max_retries=0
        ) as client:
            with pytest.raises(FatalError):
                await client.chat_image(
                    "gpt-4o", "describe", image_data=b"abc", media_type="image/png",
                )
            assert client.stats.error_count == 1
            _assert_balanced(client)


# --- chat_stream: verified existing behaviour, do not invent stats ----------


class TestChatStreamAccounting:
    """chat_stream does not go through _extract_result or the orchestrator.

    Verified: it records neither success nor error, and does not report to
    collector. This card does not add accounting — lock the current behaviour.
    """

    async def test_stream_success_does_not_record(self, httpx_mock: HTTPXMock) -> None:
        sse = (
            b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
            b"data: [DONE]\n\n"
        )
        httpx_mock.add_response(
            stream=httpx.ByteStream(sse),
            headers={"content-type": "text/event-stream"},
        )
        async with LLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            chunks = [
                chunk
                async for chunk in client.chat_stream("gpt-4o", MESSAGES)
            ]
            assert "".join(chunks) == "hi"
            assert client.stats.success_count == 0
            assert client.stats.error_count == 0
            assert client.stats.total_calls == 0

    async def test_stream_http_error_does_not_record(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(status_code=500, json={"error": "upstream"})
        async with LLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            with pytest.raises(httpx.HTTPStatusError):
                async for _ in client.chat_stream("gpt-4o", MESSAGES):
                    pass
            assert client.stats.success_count == 0
            assert client.stats.error_count == 0
            assert client.stats.total_calls == 0


# --- SyncLLMClient mirrors async stats columns ------------------------------


class TestSyncAccounting:
    def test_chat_success(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("ok"))
        with SyncLLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            client.chat("gpt-4o", MESSAGES)
            assert client.stats.success_count == 1
            _assert_balanced(client)

    def test_chat_json_parse_failure(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("not-json"))
        with SyncLLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            with pytest.raises(JSONParseError):
                client.chat_json("gpt-4o", MESSAGES, schema=TagResult)
            assert client.stats.success_count == 0
            assert client.stats.error_count == 1
            _assert_balanced(client)

    def test_chat_refusal(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_content_filter_response())
        with SyncLLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            with pytest.raises(ContentPolicyError):
                client.chat("gpt-4o", MESSAGES)
            assert client.stats.error_count == 1
            _assert_balanced(client)

    def test_chat_http_fatal(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(status_code=400, json={"error": "bad request"})
        with SyncLLMClient(
            base_url="https://api.test.com/v1", api_key="sk-test", max_retries=0
        ) as client:
            with pytest.raises(FatalError):
                client.chat("gpt-4o", MESSAGES)
            assert client.stats.error_count == 1
            _assert_balanced(client)

    def test_chat_json_success(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response('{"tags": ["s"]}'))
        with SyncLLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            result = client.chat_json("gpt-4o", MESSAGES, schema=TagResult)
            assert result.parsed.tags == ["s"]
            assert client.stats.success_count == 1
            _assert_balanced(client)

    def test_chat_image_success(self, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(json=_chat_response("img"))
        with SyncLLMClient(base_url="https://api.test.com/v1", api_key="sk-test") as client:
            client.chat_image("gpt-4o", "look", image_data=b"x", media_type="image/png")
            assert client.stats.success_count == 1
            _assert_balanced(client)
