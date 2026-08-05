from __future__ import annotations

import logging

import pytest

from llm_compat import providers
from llm_compat._base import BaseClient


class TestProviderDetectionStructure:
    @pytest.mark.parametrize(
        ("model", "expected_family", "expected_matched"),
        [
            ("deepseek-chat", "deepseek", True),
            ("deepseek-v4-flash", "deepseek", True),
            ("DeepSeek-Chat", "deepseek", True),
            ("qwen-max", "openai", False),
            (None, "openai", False),
            ("", "openai", False),
            (123, "openai", False),
        ],
    )
    def test_detect_provider_returns_family_and_match_state(
        self,
        model: object,
        expected_family: str,
        expected_matched: bool,
    ) -> None:
        result = providers.detect_provider(model)

        assert isinstance(result, providers.ProviderDetection)
        assert result.family == expected_family
        assert result.matched is expected_matched

    def test_unknown_model_still_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            result = providers.detect_provider("qwen-max")

        assert result.family == "openai"
        assert result.matched is False
        assert "unknown model" in caplog.text


class TestProviderPatternDetection:
    @pytest.mark.parametrize(
        ("pattern", "expected_family"),
        [
            ("deepseek-*", "deepseek"),
            ("gemini-2.5-*", "gemini_25"),
        ],
    )
    def test_known_pattern_returns_family(
        self,
        pattern: str,
        expected_family: str,
    ) -> None:
        result = providers.detect_provider_for_pattern(pattern)

        assert result.family == expected_family
        assert result.matched is True

    def test_broad_pattern_is_unresolved(self) -> None:
        result = providers.detect_provider_for_pattern("*")

        assert result is None


def test_partial_registered_caps_are_completed_at_caps_boundary() -> None:
    family = "acme"
    pattern_state = providers._custom_patterns
    missing = object()
    previous_caps = providers._FAMILY_CAPABILITIES.get(family, missing)
    custom_efforts = frozenset()

    try:
        providers.register_provider(
            "acme-*",
            family,
            caps={"efforts": custom_efforts},
        )

        caps = providers.get_provider_caps(family)
        assert {"disable_mode", "efforts", "supports_vision", "json_mode"} <= caps.keys()
        assert caps["efforts"] is custom_efforts
        assert caps["efforts"] is not providers._DEFAULT_CAPS["efforts"]

        payload = providers.build_request_payload(
            "acme-v1",
            "high",
            {"model": "acme-v1"},
        )
        assert payload == {"model": "acme-v1"}

        json_payload, effective_mode, _ = BaseClient(
            "http://test",
            "test-key",
        )._build_json_payload("acme-v1", [])
        assert effective_mode == "json_object"
        assert json_payload["response_format"] == {"type": "json_object"}
    finally:
        providers._custom_patterns = pattern_state
        if previous_caps is missing:
            providers._FAMILY_CAPABILITIES.pop(family, None)
        else:
            providers._FAMILY_CAPABILITIES[family] = previous_caps
