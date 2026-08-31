"""Tests for llm_compat._types — data classes."""
from __future__ import annotations

import pytest

from llm_compat._types import ChatResult, LLMStats, TokenUsage
from llm_compat.refusal import RefusalEvidence


class TestTokenUsage:
    def test_basic(self):
        u = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        assert u.prompt_tokens == 100
        assert u.total_tokens == 150

    def test_defaults(self):
        u = TokenUsage()
        assert u.prompt_tokens == 0
        assert u.total_tokens == 0

    def test_reasoning_tokens_default_is_zero(self):
        u = TokenUsage()
        assert u.reasoning_tokens == 0

    def test_reasoning_tokens_does_not_change_existing_fields(self):
        u = TokenUsage(
            prompt_tokens=2095,
            completion_tokens=5046,
            total_tokens=7141,
            reasoning_tokens=4431,
        )
        assert u.prompt_tokens == 2095
        assert u.completion_tokens == 5046
        assert u.total_tokens == 7141
        assert u.reasoning_tokens == 4431


class TestChatResult:
    def test_str_returns_content(self):
        r = ChatResult(content="hello world")
        assert str(r) == "hello world"

    def test_with_metadata(self):
        usage = TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        r = ChatResult(
            content="answer",
            usage=usage,
            latency_ms=1500,
            request_id="abc",
            model="gpt-4o",
            provider="openai_gpt4",
        )
        assert r.content == "answer"
        assert r.usage.total_tokens == 30
        assert r.latency_ms == 1500
        assert r.request_id == "abc"
        assert str(r) == "answer"

    def test_parsed_field(self):
        r = ChatResult(content='{"key": "val"}', parsed={"key": "val"})
        assert r.parsed == {"key": "val"}

    def test_refusal_metadata_defaults_and_round_trips(self):
        r = ChatResult(content="answer")
        assert r.refusal_suspected is False
        assert r.refusal_evidence is None
        evidence = RefusalEvidence(True, "text_pattern", signal="pattern:test")
        r = ChatResult(
            content="answer", refusal_suspected=True, refusal_evidence=evidence
        )
        assert r.refusal_suspected is True
        assert r.refusal_evidence == evidence

    def test_finish_reason_defaults_to_none(self):
        r = ChatResult(content="answer")
        assert r.finish_reason is None

    def test_finish_reason_round_trips(self):
        r = ChatResult(content="truncated", finish_reason="length")
        assert r.finish_reason == "length"


class TestLLMStats:
    def test_initial_zero(self):
        s = LLMStats()
        assert s.total_calls == 0
        assert s.success_count == 0
        assert s.error_count == 0

    def test_record_success(self):
        s = LLMStats()
        s.record_success(model="gpt-4o", latency_ms=1200, tokens=500)
        assert s.total_calls == 1
        assert s.success_count == 1
        assert s.total_tokens == 500
        assert s.total_latency_ms == 1200

    def test_record_error(self):
        s = LLMStats()
        s.record_error(model="gpt-4o", error_type="TimeoutError")
        assert s.total_calls == 1
        assert s.error_count == 1

    def test_success_rate(self):
        s = LLMStats()
        s.record_success(model="m", latency_ms=100, tokens=10)
        s.record_success(model="m", latency_ms=200, tokens=20)
        s.record_error(model="m", error_type="err")
        assert s.success_rate == pytest.approx(2 / 3)

    def test_success_rate_zero_calls(self):
        s = LLMStats()
        assert s.success_rate == 0.0

    def test_json_stats_initial_zero(self):
        s = LLMStats()
        assert s.json_schema_calls == 0
        assert s.json_object_calls == 0
        assert s.json_parse_failures == 0
        assert s.json_self_correction_success == 0

    def test_record_json_mode(self):
        s = LLMStats()
        s.record_json_mode("json_schema")
        s.record_json_mode("json_schema")
        s.record_json_mode("json_object")
        assert s.json_schema_calls == 2
        assert s.json_object_calls == 1

    def test_record_json_parse_failure(self):
        s = LLMStats()
        s.record_json_parse_failure()
        s.record_json_parse_failure()
        assert s.json_parse_failures == 2

    def test_record_json_self_correction(self):
        s = LLMStats()
        s.record_json_self_correction()
        assert s.json_self_correction_success == 1

    def test_reset_clears_json_stats(self):
        s = LLMStats()
        s.record_json_mode("json_schema")
        s.record_json_parse_failure()
        s.record_json_self_correction()
        s.reset()
        assert s.json_schema_calls == 0
        assert s.json_object_calls == 0
        assert s.json_parse_failures == 0
        assert s.json_self_correction_success == 0

    def test_reset(self):
        s = LLMStats()
        s.record_success(model="m", latency_ms=100, tokens=10)
        s.reset()
        assert s.total_calls == 0
