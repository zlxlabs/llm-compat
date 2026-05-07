"""Tests for llm_compat._compat — effort normalization and config validation."""
from __future__ import annotations

import logging

import pytest

from llm_compat._compat import normalize_reasoning_effort, validate_config


class TestNormalizeReasoningEffort:
    def test_none_passthrough(self) -> None:
        assert normalize_reasoning_effort(None) is None

    def test_empty_string_to_none(self) -> None:
        assert normalize_reasoning_effort("") is None

    def test_whitespace_to_none(self) -> None:
        assert normalize_reasoning_effort("  ") is None

    def test_none_string_to_disabled(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            result = normalize_reasoning_effort("none")
        assert result == "disabled"
        assert "deprecat" in caplog.text.lower()

    def test_disabled_passthrough(self) -> None:
        assert normalize_reasoning_effort("disabled") == "disabled"

    def test_high_passthrough(self) -> None:
        assert normalize_reasoning_effort("high") == "high"

    def test_max_passthrough(self) -> None:
        assert normalize_reasoning_effort("max") == "max"

    def test_strips_whitespace(self) -> None:
        assert normalize_reasoning_effort(" high ") == "high"


class TestValidateConfig:
    def test_valid_config_no_warnings(self) -> None:
        warnings = validate_config("deepseek-v4-flash", "high")
        assert warnings == []

    def test_gpt4_with_effort_warns(self) -> None:
        warnings = validate_config("gpt-4o", "high")
        assert len(warnings) == 1
        assert "not support" in warnings[0].lower() or "drop" in warnings[0].lower()

    def test_none_effort_always_ok(self) -> None:
        warnings = validate_config("gpt-4o", None)
        assert warnings == []

    def test_disabled_on_unsupported_warns(self) -> None:
        warnings = validate_config("o3-mini", "disabled")
        assert len(warnings) >= 1

    def test_max_on_gemini_warns_clamp(self) -> None:
        warnings = validate_config("gemini-2.5-flash", "max")
        assert len(warnings) == 1
        assert "clamp" in warnings[0].lower()

    def test_valid_disabled_on_deepseek(self) -> None:
        warnings = validate_config("deepseek-v4-flash", "disabled")
        assert warnings == []
