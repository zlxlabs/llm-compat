from __future__ import annotations

from llm_compat._compat import validate_fallback_config


class TestValidateFallbackConfig:
    def test_valid_config(self):
        config = {"deepseek-*": ["gpt-4.1-mini"]}
        warnings = validate_fallback_config(config)
        assert warnings == []

    def test_unrecognized_fallback_model_defaults_to_openai(self):
        config = {"deepseek-*": ["unknown-model-xyz"]}
        warnings = validate_fallback_config(config)
        assert warnings == []

    def test_vision_primary_no_vision_fallback(self):
        config = {"gpt-4.1-*": ["deepseek-chat"]}
        warnings = validate_fallback_config(config)
        assert any("vision" in w.lower() for w in warnings)

    def test_vision_primary_with_vision_fallback(self):
        config = {"gpt-4.1-*": ["gemini-2.5-flash"]}
        warnings = validate_fallback_config(config)
        vision_warnings = [w for w in warnings if "vision" in w.lower()]
        assert vision_warnings == []

    def test_empty_config(self):
        warnings = validate_fallback_config({})
        assert warnings == []

    def test_none_config(self):
        warnings = validate_fallback_config(None)
        assert warnings == []
