"""Tests for llm_compat._compat — effort normalization and config validation."""
from __future__ import annotations

import logging

import pytest

from llm_compat import providers
from llm_compat._compat import normalize_reasoning_effort, validate_config

_VALIDATION_FAMILY_MODELS: dict[str, str] = {
    "deepseek": "deepseek-v4-flash",
    "gemini_25": "gemini-2.5-flash",
    "gemini_3": "gemini-3-flash",
    "gemini": "gemini-pro",
    "openai_gpt5": "gpt-5",
    "openai_o": "o3-mini",
    "doubao_seed": "doubao-seed-2.0",
    "doubao": "doubao-pro-256k",
    "openai_gpt4": "gpt-4o",
    "openai": "qwen-turbo",
    "unknown": "unknown-model",
}


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
    def test_covers_every_provider_family_and_unknown(self) -> None:
        assert set(_VALIDATION_FAMILY_MODELS) == set(providers._FAMILY_CAPABILITIES) | {"unknown"}
        assert set(providers._EFFORT_RANK) == {
            "minimal",
            "low",
            "medium",
            "high",
            "max",
            "xhigh",
        }

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

    @pytest.mark.parametrize("family,model", _VALIDATION_FAMILY_MODELS.items())
    @pytest.mark.parametrize("effort", providers._EFFORT_RANK)
    def test_clamp_prediction_matches_translation(
        self, family: str, model: str, effort: str
    ) -> None:
        warnings = validate_config(model, effort)
        actual = providers._translate(family, effort).get("reasoning_effort")

        if actual is None:
            assert len(warnings) == 1
            assert "dropped at runtime" in warnings[0]
        elif actual == effort:
            assert warnings == []
        else:
            assert len(warnings) == 1
            assert f"will clamp to '{actual}' at runtime" in warnings[0]

    @pytest.mark.parametrize("family,model", _VALIDATION_FAMILY_MODELS.items())
    @pytest.mark.parametrize("effort", ["disabled", "none"])
    def test_disable_alias_prediction_matches_translation(
        self, family: str, model: str, effort: str
    ) -> None:
        warnings = validate_config(model, effort)
        payload = providers._translate(family, effort)
        mode = providers.get_provider_caps(family)["disable_mode"]

        if mode == "unsupported":
            assert len(warnings) == 1
            assert "dropped at runtime" in warnings[0]
            assert payload == {}
        else:
            assert warnings == []
