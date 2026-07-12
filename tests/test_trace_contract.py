"""Public contract tests for model-level call traces."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from llm_compat import (
    CallTrace,
    ChatResult,
    ContentPolicyError,
    FatalError,
    JSONParseError,
    LLMCallError,
    ModelAttempt,
    RetryableError,
    RouteDecision,
    SkipRequestError,
)
from llm_compat._trace import _CallTraceBuilder


def test_trace_public_types_are_frozen_and_serialize_to_safe_scalars() -> None:
    decision = RouteDecision(model="primary", action="selected", reason="prescan_miss")
    attempt = ModelAttempt(
        model="primary",
        provider="openai",
        json_mode="json_schema",
        trigger="primary",
        outcome="response_received",
        latency_ms=12,
    )
    trace = CallTrace(
        request_id="req-1",
        requested_model="primary",
        started_at="2026-07-12T12:00:00+00:00",
        latency_ms=14,
        route_decisions=(decision,),
        model_attempts=(attempt,),
        final_outcome="success",
        final_model="primary",
    )

    with pytest.raises(FrozenInstanceError):
        trace.final_outcome = "error"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        attempt.outcome = "error"  # type: ignore[misc]

    serialized = trace.to_dict()
    assert serialized["route_decisions"] == [
        {"model": "primary", "action": "selected", "reason": "prescan_miss"}
    ]
    assert serialized["model_attempts"][0]["outcome"] == "response_received"
    forbidden = ("prompt", "messages", "payload", "headers", "api_key", "raw_content")
    assert not any(secret in repr(serialized).lower() for secret in forbidden)


def test_trace_builder_truncates_model_events_explicitly() -> None:
    builder = _CallTraceBuilder(request_id="req-2", requested_model="primary")
    for ordinal in range(105):
        builder.add_model_attempt(
            model=f"model-{ordinal}",
            provider="openai",
            json_mode="text",
            trigger="self_correction",
            outcome="response_received",
            latency_ms=1,
        )

    trace = builder.freeze(final_outcome="success", final_model="model-104")
    assert len(trace.model_attempts) == 100
    assert trace.truncated is True
    assert trace.dropped_events == 5


def test_error_hierarchy_and_stable_metadata_are_backward_compatible() -> None:
    assert issubclass(FatalError, LLMCallError)
    assert issubclass(RetryableError, LLMCallError)
    assert issubclass(ContentPolicyError, LLMCallError)
    assert issubclass(JSONParseError, LLMCallError)
    assert not issubclass(SkipRequestError, LLMCallError)

    error = FatalError("bad request", error_kind="invalid_request", http_status=400)
    assert error.error_kind == "invalid_request"
    assert error.http_status == 400
    assert error.trace is None


def test_chat_result_trace_remains_optional() -> None:
    result = ChatResult(content="legacy")
    assert result.trace is None
