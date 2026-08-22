"""Tests for content fallback integration in LLMClient."""
from __future__ import annotations

import json
import logging

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


def _content_filter_response(content: str = "provider partial output") -> dict:
    return {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "content_filter",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10},
    }


def _message_refusal_response(refusal: str = "provider refusal") -> dict:
    response = _chat_response("provider partial output")
    response["choices"][0]["message"]["refusal"] = refusal
    return response


MESSAGES = [{"role": "user", "content": "hello"}]


STRUCTURED_NO_FALLBACK_CASES = [
    ("finish_reason=content_filter", _content_filter_response()),
    (
        "finish_reason=content_policy",
        _chat_response("provider partial output", finish_reason="content_policy"),
    ),
    ("finish_reason=safety", _chat_response("provider partial output", finish_reason="safety")),
    ("message.refusal", _message_refusal_response()),
]


class TestFallbackBasic:
    @pytest.mark.parametrize(
        ("signal", "response"),
        STRUCTURED_NO_FALLBACK_CASES,
        ids=[case[0] for case in STRUCTURED_NO_FALLBACK_CASES],
    )
    async def test_structured_refusal_without_fallback_raises(
        self, httpx_mock: HTTPXMock, signal: str, response: dict
    ):
        httpx_mock.add_response(json=response)
        async with LLMClient(
            base_url="https://api.test.com/v1", api_key="sk-test"
        ) as client:
            with pytest.raises(ContentPolicyError) as exc_info:
                await client.chat("gpt-4o", MESSAGES)

        error = exc_info.value
        assert error.evidence is not None
        assert error.evidence.layer == "structured_signal"
        assert error.evidence.signal == signal
        assert error.attempt_layers == {"gpt-4o": "structured_signal"}

    async def test_refusal_warning_contains_evidence_fields(
        self, httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
    ):
        httpx_mock.add_response(json=_refusal_response())
        httpx_mock.add_response(json=_refusal_response())
        with caplog.at_level("WARNING"):
            async with LLMClient(
                base_url="https://api.test.com/v1",
                api_key="sk-test",
                content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
            ) as client:
                result = await client.chat("deepseek-v4", MESSAGES)
        warning_records = [
            record for record in caplog.records if record.levelno == logging.WARNING
        ]
        assert warning_records
        assert "Refusal detected" in warning_records[0].message
        evidence = result.refusal_evidence
        assert evidence is not None
        assert evidence.layer == "text_pattern"
        assert evidence.signal == "pattern:cn_first_person_cannot"
        assert evidence.match_position == 0
        assert evidence.content_length == 8
        assert evidence.finish_reason == "stop"

    async def test_replace_mode_disables_builtin_text_patterns(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(json=_refusal_response("I cannot assist"))
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
            refusal_keywords_mode="replace",
        ) as client:
            result = await client.chat("deepseek-v4", MESSAGES)
        assert result.content == "I cannot assist"
        assert len(httpx_mock.get_requests()) == 1

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
        assert result.trace is not None
        assert result.trace.final_outcome == "content_policy_recovered"

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
        partial = "provider partial output " + "x" * 500
        httpx_mock.add_response(json=_content_filter_response(partial))
        httpx_mock.add_response(json=_content_filter_response(partial))
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

    async def test_empty_structured_refusal_content_is_not_rescued(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(json=_content_filter_response(""))
        httpx_mock.add_response(json=_content_filter_response(""))
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
        structured_content = "provider partial output " + "x" * 500
        httpx_mock.add_response(json=_content_filter_response(structured_content))
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

    async def test_malformed_candidates_without_content_are_not_rescued(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(json={"choices": []})
        httpx_mock.add_response(json={"choices": []})
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
        ) as client:
            with pytest.raises(ContentPolicyError, match="malformed") as exc_info:
                await client.chat("deepseek-v4", MESSAGES)
        assert exc_info.value.attempt_layers == {
            "deepseek-v4": "malformed",
            "gpt-4.1-mini": "malformed",
        }

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

    async def test_http_content_policy_fallback_logs_and_reports_layer(
        self, httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
    ):
        httpx_mock.add_response(
            status_code=400,
            json={"error": {"message": "content_policy_violation"}},
        )
        httpx_mock.add_response(
            status_code=400,
            json={"error": {"message": "content_policy_violation"}},
        )
        with caplog.at_level("WARNING"):
            async with LLMClient(
                base_url="https://api.test.com/v1",
                api_key="sk-test",
                content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
                max_retries=0,
                on_all_refused="raise",
            ) as client:
                with pytest.raises(ContentPolicyError) as exc_info:
                    await client.chat("deepseek-v4", MESSAGES)

        refusal_warnings = [
            record.message
            for record in caplog.records
            if record.levelno == logging.WARNING and "Refusal detected" in record.message
        ]
        assert len(refusal_warnings) == 2
        assert all("layer=http_error" in message for message in refusal_warnings)
        assert all("position=-1" in message for message in refusal_warnings)
        assert all("content_length=0" in message for message in refusal_warnings)
        assert all("finish_reason=None" in message for message in refusal_warnings)
        assert exc_info.value.attempt_layers == {
            "deepseek-v4": "http_error",
            "gpt-4.1-mini": "http_error",
        }

    async def test_http_content_policy_without_fallback_logs_and_reports_layer(
        self, httpx_mock: HTTPXMock, caplog: pytest.LogCaptureFixture
    ):
        httpx_mock.add_response(
            status_code=400,
            json={"error": {"message": "content_policy_violation"}},
        )
        with caplog.at_level("WARNING"):
            async with LLMClient(
                base_url="https://api.test.com/v1",
                api_key="sk-test",
                max_retries=0,
            ) as client:
                with pytest.raises(ContentPolicyError) as exc_info:
                    await client.chat("gpt-4o", MESSAGES)

        refusal_warnings = [
            record.message
            for record in caplog.records
            if record.levelno == logging.WARNING and "Refusal detected" in record.message
        ]
        assert len(refusal_warnings) == 1
        assert "model=gpt-4o" in refusal_warnings[0]
        assert "layer=http_error" in refusal_warnings[0]
        assert "matched_text=''" in refusal_warnings[0]
        assert "position=-1" in refusal_warnings[0]
        assert "content_length=0" in refusal_warnings[0]
        assert "finish_reason=None" in refusal_warnings[0]
        assert exc_info.value.attempt_layers == {"gpt-4o": "http_error"}
        assert "gpt-4o=http_error" in str(exc_info.value)

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

    async def test_rescue_without_usage_records_success(self, httpx_mock: HTTPXMock):
        first = _refusal_response()
        first.pop("usage")
        second = _refusal_response("I cannot assist")
        second.pop("usage")
        httpx_mock.add_response(json=first)
        httpx_mock.add_response(json=second)
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
        ) as client:
            result = await client.chat("deepseek-v4", MESSAGES)
            assert result.refusal_suspected is True
            assert client.stats.success_count == 1
            assert client.stats.total_calls == 1

    async def test_json_rescue_validation_failure_records_error_not_success(
        self, httpx_mock: HTTPXMock
    ):
        httpx_mock.add_response(json=_refusal_response())
        httpx_mock.add_response(json=_refusal_response("I cannot assist"))
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
        ) as client:
            with pytest.raises(ContentPolicyError, match="best candidate rescue failed"):
                await client.chat_json("deepseek-v4", MESSAGES)
            assert client.stats.success_count == 0
            assert client.stats.error_count == 1
            assert client.stats.total_calls == 1

    async def test_json_rescue_failure_records_error_once_gate_contract(
        self, httpx_mock: HTTPXMock
    ):
        """Gate finding correctness-json-rescue-double-error-accounting contract."""
        httpx_mock.add_response(json=_refusal_response("我无法回答这个问题。"))
        httpx_mock.add_response(json=_refusal_response("I cannot assist with that."))
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
        ) as client:
            with pytest.raises(ContentPolicyError):
                await client.chat_json("deepseek-v4", MESSAGES)
            assert client.stats.success_count == 0
            assert client.stats.error_count == 1
            assert client.stats.total_calls == 1

    async def test_refusal_rescue_success_records_success_once_gate_contract(
        self, httpx_mock: HTTPXMock
    ):
        """Gate finding correctness-json-rescue-double-error-accounting contract."""
        httpx_mock.add_response(json=_refusal_response("我无法回答这个问题。"))
        httpx_mock.add_response(json=_refusal_response("I cannot assist with that."))
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
            content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
        ) as client:
            result = await client.chat("deepseek-v4", MESSAGES)
            assert result.content == "I cannot assist with that."
            assert result.refusal_suspected is True
            assert client.stats.success_count == 1
            assert client.stats.error_count == 0
            assert client.stats.total_calls == 1

    async def test_empty_chain_skips_refusal_error_accounting_gate_contract(
        self, httpx_mock: HTTPXMock
    ):
        """Gate finding correctness-json-rescue-double-error-accounting: with an empty
        chain, refusal detection does not run, so _base.py:853 and _base.py:619
        cannot both call record_error.
        """
        refusal = "我无法回答这个问题。"
        httpx_mock.add_response(json=_refusal_response(refusal))
        async with LLMClient(
            base_url="https://api.test.com/v1",
            api_key="sk-test",
        ) as client:
            result = await client.chat("deepseek-v4", MESSAGES)
            assert result.content == refusal
            assert result.refusal_suspected is False
            assert client.stats.error_count == 0

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
