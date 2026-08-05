from __future__ import annotations

import logging

import pytest

from llm_compat import providers


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
