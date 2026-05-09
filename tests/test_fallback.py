from __future__ import annotations

from llm_compat.fallback import filter_by_modality, resolve_fallback_chain
from llm_compat.providers import get_provider_caps


class TestSupportsVision:
    def test_openai_gpt4_supports_vision(self):
        caps = get_provider_caps("openai_gpt4")
        assert caps["supports_vision"] is True

    def test_deepseek_no_vision(self):
        caps = get_provider_caps("deepseek")
        assert caps["supports_vision"] is False

    def test_gemini_25_supports_vision(self):
        caps = get_provider_caps("gemini_25")
        assert caps["supports_vision"] is True

    def test_unknown_defaults_to_vision(self):
        caps = get_provider_caps("unknown_family")
        assert caps["supports_vision"] is True


class TestResolveFallbackChain:

    def test_pattern_match(self):
        config = {"deepseek-*": ["gpt-4.1-mini", "gemini-2.5-flash"]}
        chain = resolve_fallback_chain("deepseek-v4", config)
        assert chain == ["gpt-4.1-mini", "gemini-2.5-flash"]

    def test_no_match(self):
        config = {"deepseek-*": ["gpt-4.1-mini"]}
        chain = resolve_fallback_chain("gpt-4o", config)
        assert chain is None

    def test_first_match_wins(self):
        config = {
            "deepseek-chat": ["gpt-4o"],
            "deepseek-*": ["gpt-4.1-mini"],
        }
        chain = resolve_fallback_chain("deepseek-chat", config)
        assert chain == ["gpt-4o"]

    def test_empty_config(self):
        chain = resolve_fallback_chain("deepseek-v4", {})
        assert chain is None

    def test_none_config(self):
        chain = resolve_fallback_chain("deepseek-v4", None)
        assert chain is None


class TestFilterByModality:

    def test_vision_request_skips_text_only(self):
        chain = ["deepseek-chat", "gpt-4.1-mini", "gemini-2.5-flash"]
        filtered = filter_by_modality(chain, needs_vision=True)
        assert "deepseek-chat" not in filtered
        assert "gpt-4.1-mini" in filtered

    def test_text_request_keeps_all(self):
        chain = ["deepseek-chat", "gpt-4.1-mini"]
        filtered = filter_by_modality(chain, needs_vision=False)
        assert filtered == chain

    def test_empty_chain(self):
        filtered = filter_by_modality([], needs_vision=True)
        assert filtered == []

    def test_unknown_model_kept_for_vision(self):
        chain = ["unknown-model-xyz"]
        filtered = filter_by_modality(chain, needs_vision=True)
        assert filtered == ["unknown-model-xyz"]
