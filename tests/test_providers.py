"""Tests for llm_compat.providers — provider detection, thinking translation, payload building."""
from __future__ import annotations

import logging

import pytest

from llm_compat import providers
from llm_compat._base import BaseClient
from llm_compat._compat import validate_config

_EFFORT_CLAMP_MATRIX: dict[str, dict[str, str | None]] = {
    "deepseek": {
        "minimal": "low",
        "low": "low",
        "medium": "high",
        "high": "high",
        "max": "max",
        "xhigh": "xhigh",
    },
    "gemini_25": {
        "minimal": "low",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "max": "high",
        "xhigh": "high",
    },
    "gemini_3": {
        "minimal": "minimal",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "max": "high",
        "xhigh": "high",
    },
    "gemini": {
        "minimal": "low",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "max": "high",
        "xhigh": "high",
    },
    "openai_gpt5": {
        "minimal": "minimal",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "max": "high",
        "xhigh": "high",
    },
    "openai_o": {
        "minimal": "low",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "max": "high",
        "xhigh": "high",
    },
    "doubao_seed": {
        "minimal": "minimal",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "max": "high",
        "xhigh": "high",
    },
    "doubao": {
        "minimal": None,
        "low": None,
        "medium": None,
        "high": None,
        "max": None,
        "xhigh": None,
    },
    "openai": {
        "minimal": "low",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "max": "high",
        "xhigh": "high",
    },
    "openai_gpt4": {
        "minimal": None,
        "low": None,
        "medium": None,
        "high": None,
        "max": None,
        "xhigh": None,
    },
}


def _complete_custom_caps() -> dict[str, object]:
    return {
        "disable_mode": "na",
        "efforts": frozenset({"low"}),
        "supports_vision": True,
        "json_mode": "json_object",
    }


class TestEffortClampMatrix:
    def test_covers_every_provider_family(self) -> None:
        assert set(_EFFORT_CLAMP_MATRIX) == set(providers._FAMILY_CAPABILITIES)

    @pytest.mark.parametrize("family,expected_by_effort", _EFFORT_CLAMP_MATRIX.items())
    def test_all_families_and_efforts(
        self,
        family: str,
        expected_by_effort: dict[str, str | None],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        accepted = providers.get_provider_caps(family)["efforts"]
        assert set(expected_by_effort) == set(providers._EFFORT_RANK)

        for requested, expected in expected_by_effort.items():
            caplog.clear()
            with caplog.at_level(logging.WARNING):
                payload = providers._translate(family, requested)

            if expected is None:
                assert payload == {}
                assert "effort unsupported" in caplog.text
                continue

            assert expected in accepted
            assert payload == {"reasoning_effort": expected}

            requested_rank = providers._EFFORT_RANK[requested]
            actual_rank = providers._EFFORT_RANK[expected]
            if requested in accepted:
                assert not caplog.records
            else:
                assert caplog.records
                expected_direction = "upward" if actual_rank > requested_rank else "downward"
                assert f"direction={expected_direction}" in caplog.text

            assert abs(actual_rank - requested_rank) <= min(
                abs(providers._EFFORT_RANK[candidate] - requested_rank)
                for candidate in accepted
            )

    def test_deepseek_medium_clamps_to_high_not_xhigh(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        base = {"model": "deepseek-v4-flash", "messages": []}
        with caplog.at_level(logging.WARNING):
            payload = providers.build_request_payload("deepseek-v4-flash", "medium", base)

        assert payload["reasoning_effort"] == "high"
        assert "medium" not in providers.get_provider_caps("deepseek")["efforts"]
        assert "requested='medium' -> actual='high'; direction=upward" in caplog.text


class TestCustomProviderEfforts:
    def test_register_rejects_unranked_effort(self) -> None:
        family = "vendor_unranked"
        caps = {
            "disable_mode": "na",
            "efforts": frozenset({"low", "ultra"}),
            "supports_vision": False,
            "json_mode": "json_object",
        }

        with pytest.raises(ValueError, match=r"vendor_unranked.*ultra"):
            providers.register_provider("vendor-unranked-*", family, caps=caps)

        assert family not in providers._FAMILY_CAPABILITIES

    @pytest.mark.parametrize(
        "missing_key",
        ["disable_mode", "efforts", "supports_vision", "json_mode"],
    )
    def test_register_rejects_each_missing_required_cap_without_state_change(
        self, missing_key: str
    ) -> None:
        family = f"vendor_missing_{missing_key}"
        caps = _complete_custom_caps()
        caps.pop(missing_key)
        before_patterns = (
            tuple(providers._custom_patterns)
            if providers._custom_patterns is not None
            else None
        )
        before_caps = dict(providers._FAMILY_CAPABILITIES)

        with pytest.raises(ValueError, match=rf"missing required caps keys:.*{missing_key}"):
            providers.register_provider(f"{family}-*", family, caps=caps)

        assert providers._custom_patterns == before_patterns
        assert before_caps == providers._FAMILY_CAPABILITIES

    @pytest.mark.parametrize(
        ("field", "value", "error_fragment"),
        [
            ("disable_mode", "invalid", "invalid disable_mode"),
            ("json_mode", "invalid", "invalid json_mode"),
            ("supports_vision", "true", "non-bool supports_vision"),
        ],
    )
    def test_register_rejects_invalid_cap_values(
        self,
        field: str,
        value: object,
        error_fragment: str,
    ) -> None:
        family = f"vendor_invalid_{field}"
        caps = _complete_custom_caps()
        caps[field] = value

        with pytest.raises(ValueError, match=error_fragment):
            providers.register_provider(f"{family}-*", family, caps=caps)

        assert family not in providers._FAMILY_CAPABILITIES

    def test_register_complete_caps_detects_provider_and_returns_caps(self) -> None:
        family = "vendor_complete"
        pattern_state = providers._custom_patterns
        previous_caps = providers._FAMILY_CAPABILITIES.get(family)
        caps = _complete_custom_caps()

        try:
            providers.register_provider("vendor-complete-*", family, caps=caps)

            assert providers.detect_provider("vendor-complete-v1").family == family
            assert providers.get_provider_caps(family) == caps
        finally:
            providers._custom_patterns = pattern_state
            if previous_caps is None:
                providers._FAMILY_CAPABILITIES.pop(family, None)
            else:
                providers._FAMILY_CAPABILITIES[family] = previous_caps

    def test_register_isolates_callers_caps_dict(self) -> None:
        family = "vendor_isolated"
        pattern_state = providers._custom_patterns
        previous_caps = providers._FAMILY_CAPABILITIES.get(family)
        caps = _complete_custom_caps()

        try:
            providers.register_provider("vendor-isolated-*", family, caps=caps)
            caps["json_mode"] = "json_schema"

            assert providers.get_provider_caps(family)["json_mode"] == "json_object"
        finally:
            providers._custom_patterns = pattern_state
            if previous_caps is None:
                providers._FAMILY_CAPABILITIES.pop(family, None)
            else:
                providers._FAMILY_CAPABILITIES[family] = previous_caps

    def test_register_without_caps_only_registers_pattern(self) -> None:
        family = "vendor_pattern_only"
        pattern_state = providers._custom_patterns
        before_caps = dict(providers._FAMILY_CAPABILITIES)

        try:
            providers.register_provider("vendor-pattern-only-*", family)

            assert providers.detect_provider("vendor-pattern-only-v1").family == family
            assert before_caps == providers._FAMILY_CAPABILITIES
        finally:
            providers._custom_patterns = pattern_state

    def test_set_custom_patterns_copies_input_sequence(self) -> None:
        patterns = [["vendor-sequence-*", "deepseek"]]

        providers.set_custom_patterns(patterns)
        try:
            patterns.append(["another-*", "openai"])

            assert providers.detect_provider("vendor-sequence-v1").family == "deepseek"
            assert providers.detect_provider("another-v1").family == "openai"
        finally:
            providers.set_custom_patterns(None)

    def test_resolver_handles_unranked_effort_after_direct_capability_mutation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        caps = dict(providers._FAMILY_CAPABILITIES["openai"])
        caps["efforts"] = frozenset({"low", "ultra"})
        monkeypatch.setitem(providers._FAMILY_CAPABILITIES, "vendor_mutated", caps)
        monkeypatch.setattr(
            providers,
            "_custom_patterns",
            (("vendor-mutated-*", "vendor_mutated"),),
        )

        assert providers.resolve_effort_clamp("vendor_mutated", "high") == "ultra"
        assert validate_config("vendor-mutated-model", "high")
        payload = providers.build_request_payload(
            "vendor-mutated-model",
            "high",
            {"model": "vendor-mutated-model", "messages": []},
        )
        assert payload["reasoning_effort"] == "ultra"


class TestDetectProvider:
    @pytest.mark.parametrize(
        "model,expected",
        [
            ("deepseek-v4-flash", "deepseek"),
            ("deepseek-chat", "deepseek"),
            ("deepseek-reasoner", "deepseek"),
            ("gemini-2.5-flash", "gemini_25"),
            ("gemini-2.5-pro", "gemini_25"),
            ("gemini-3-flash", "gemini_3"),
            ("gemini-3-flash-preview", "gemini_3"),
            ("gemini-3.5-pro", "gemini_3"),
            ("gemini-pro", "gemini"),
            ("gpt-5", "openai_gpt5"),
            ("gpt-5-turbo", "openai_gpt5"),
            ("gpt-5.1", "openai_gpt5"),
            ("gpt-4o", "openai_gpt4"),
            ("gpt-4.1-mini", "openai_gpt4"),
            ("o1-pro", "openai_o"),
            ("o3-mini", "openai_o"),
            ("o4-mini", "openai_o"),
            ("qwen-turbo", "openai"),
            ("glm-4", "openai"),
        ],
    )
    def test_family_recognition(self, model: str, expected: str) -> None:
        assert providers.detect_provider(model).family == expected

    def test_case_insensitive(self) -> None:
        assert providers.detect_provider("DeepSeek-V4-Flash").family == "deepseek"
        assert providers.detect_provider("Gemini-3-Flash").family == "gemini_3"

    def test_empty_model_returns_openai_with_warn(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            assert providers.detect_provider("").family == "openai"
        assert caplog.text

    def test_none_model_returns_openai(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            assert providers.detect_provider(None).family == "openai"  # type: ignore[arg-type]

    def test_unknown_model_returns_openai(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            assert providers.detect_provider("unknown-model-xyz").family == "openai"

    def test_custom_patterns_override(self) -> None:
        custom = (("myproxy-*", "deepseek"),)
        assert providers.detect_provider("myproxy-v1", custom).family == "deepseek"
        assert providers.detect_provider("myproxy-v1").family == "openai"

    def test_set_custom_patterns_dict(self) -> None:
        providers.set_custom_patterns({"dsproxy-*": "deepseek"})
        try:
            assert providers.detect_provider("dsproxy-alpha").family == "deepseek"
            assert providers.detect_provider("gpt-4o").family == "openai_gpt4"
        finally:
            providers.set_custom_patterns(None)
        assert providers.detect_provider("dsproxy-alpha").family == "openai"

    def test_set_custom_patterns_list(self) -> None:
        providers.set_custom_patterns([["myai-*", "deepseek"]])
        try:
            assert providers.detect_provider("myai-v1").family == "deepseek"
        finally:
            providers.set_custom_patterns(None)

    def test_invalid_patterns_ignored(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            providers.set_custom_patterns("not a list")
        assert providers.detect_provider("gpt-4o").family == "openai_gpt4"


class TestBuildPayloadDeepSeek:
    def _base(self, model: str = "deepseek-v4-flash") -> dict:
        return {"model": model, "messages": [{"role": "user", "content": "hi"}]}

    def test_disabled_sets_thinking(self) -> None:
        payload = providers.build_request_payload("deepseek-v4-flash", "disabled", self._base())
        assert payload["thinking"] == {"type": "disabled"}
        assert "extra_body" not in payload
        assert "reasoning_effort" not in payload

    def test_stripped_none_disables_deepseek_at_public_boundary(self) -> None:
        payload = providers.build_request_payload("deepseek-v4-flash", " none ", self._base())
        assert payload["thinking"] == {"type": "disabled"}
        assert "reasoning_effort" not in payload

    def test_high_sets_reasoning_effort(self) -> None:
        payload = providers.build_request_payload("deepseek-v4-flash", "high", self._base())
        assert payload["reasoning_effort"] == "high"
        assert "extra_body" not in payload

    def test_max_passthrough(self) -> None:
        payload = providers.build_request_payload("deepseek-v4-flash", "max", self._base())
        assert payload["reasoning_effort"] == "max"

    def test_none_sends_no_extra(self) -> None:
        payload = providers.build_request_payload("deepseek-v4-flash", None, self._base())
        assert "reasoning_effort" not in payload
        assert "extra_body" not in payload

    @pytest.mark.parametrize(
        "effort",
        [
            None,
            "",
            "  ",
            "none",
            " NONE ",
            "disabled",
            " DISABLED ",
            "off",
            " OFF ",
            "false",
            " FALSE ",
            "minimal",
            " MINIMAL ",
            "low",
            " LOW ",
            "medium",
            " Medium ",
            "high",
            " HIGH ",
            "max",
            " MAX ",
            "xhigh",
            " XHIGH ",
        ],
    )
    def test_public_and_base_payloads_share_normalization(self, effort: str | None) -> None:
        model = "deepseek-v4-flash"
        messages: list[dict[str, object]] = []
        base = {"model": model, "messages": messages}

        public_payload = providers.build_request_payload(model, effort, base)
        client_payload = BaseClient("http://test", "test-key")._build_payload(
            model, messages, effort
        )

        assert public_payload == client_payload

    def test_minimal_clamps(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            payload = providers.build_request_payload(
                "deepseek-v4-flash", "minimal", self._base()
            )
        assert payload.get("reasoning_effort") == "low"

    def test_existing_extra_body_is_preserved_alongside_translation(self) -> None:
        base = {**self._base(), "extra_body": {"foo": "bar"}}
        payload = providers.build_request_payload("deepseek-v4-flash", "disabled", base)
        assert payload["extra_body"]["foo"] == "bar"
        assert payload["thinking"]["type"] == "disabled"


class TestBuildPayloadGemini25:
    def _base(self) -> dict:
        return {"model": "gemini-2.5-flash", "messages": []}

    def test_disabled_sets_effort_none(self) -> None:
        payload = providers.build_request_payload("gemini-2.5-flash", "disabled", self._base())
        assert payload["reasoning_effort"] == "none"

    def test_high_passthrough(self) -> None:
        payload = providers.build_request_payload("gemini-2.5-flash", "high", self._base())
        assert payload["reasoning_effort"] == "high"

    def test_max_clamps_to_high(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            payload = providers.build_request_payload("gemini-2.5-flash", "max", self._base())
        assert payload["reasoning_effort"] == "high"


class TestBuildPayloadGemini3:
    def _base(self) -> dict:
        return {"model": "gemini-3-flash", "messages": []}

    def test_disabled_falls_to_minimal(self) -> None:
        payload = providers.build_request_payload("gemini-3-flash", "disabled", self._base())
        assert payload["reasoning_effort"] == "minimal"

    def test_minimal_passthrough(self) -> None:
        payload = providers.build_request_payload("gemini-3-flash", "minimal", self._base())
        assert payload["reasoning_effort"] == "minimal"


class TestBuildPayloadOpenAIGPT5:
    def _base(self) -> dict:
        return {"model": "gpt-5", "messages": []}

    def test_disabled_falls_to_minimal(self) -> None:
        payload = providers.build_request_payload("gpt-5", "disabled", self._base())
        assert payload["reasoning_effort"] == "minimal"

    def test_high_passthrough(self) -> None:
        payload = providers.build_request_payload("gpt-5", "high", self._base())
        assert payload["reasoning_effort"] == "high"


class TestBuildPayloadOpenAIGPT4:
    def _base(self) -> dict:
        return {"model": "gpt-4o", "messages": []}

    def test_disabled_is_na(self) -> None:
        payload = providers.build_request_payload("gpt-4o", "disabled", self._base())
        assert "reasoning_effort" not in payload
        assert "extra_body" not in payload

    def test_any_effort_dropped(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            payload = providers.build_request_payload("gpt-4o", "high", self._base())
        assert "reasoning_effort" not in payload


class TestBuildPayloadOpenAIO:
    def _base(self) -> dict:
        return {"model": "o3-mini", "messages": []}

    def test_disabled_dropped_with_warn(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            payload = providers.build_request_payload("o3-mini", "disabled", self._base())
        assert "reasoning_effort" not in payload

    def test_high_passthrough(self) -> None:
        payload = providers.build_request_payload("o3-mini", "high", self._base())
        assert payload["reasoning_effort"] == "high"


class TestDetectProviderDoubao:
    @pytest.mark.parametrize(
        "model,expected",
        [
            ("doubao-seed-1.8", "doubao_seed"),
            ("doubao-seed-2.0", "doubao_seed"),
            ("doubao-seed-2-0-pro-260215", "doubao_seed"),
            ("doubao-seed-2-0-lite-260428", "doubao_seed"),
            ("doubao-pro-256k", "doubao"),
            ("doubao-lite-128k", "doubao"),
            ("doubao-vision-pro-32k", "doubao"),
        ],
    )
    def test_doubao_family_recognition(self, model: str, expected: str) -> None:
        assert providers.detect_provider(model).family == expected


class TestBuildPayloadDoubaoSeed:
    def _base(self, model: str = "doubao-seed-2.0") -> dict:
        return {"model": model, "messages": [{"role": "user", "content": "hi"}]}

    def test_disabled_uses_minimal(self) -> None:
        payload = providers.build_request_payload("doubao-seed-2.0", "disabled", self._base())
        assert payload["reasoning_effort"] == "minimal"

    def test_high_passthrough(self) -> None:
        payload = providers.build_request_payload("doubao-seed-2.0", "high", self._base())
        assert payload["reasoning_effort"] == "high"

    def test_minimal_passthrough(self) -> None:
        payload = providers.build_request_payload("doubao-seed-2.0", "minimal", self._base())
        assert payload["reasoning_effort"] == "minimal"

    def test_none_sends_no_extra(self) -> None:
        payload = providers.build_request_payload("doubao-seed-2.0", None, self._base())
        assert "reasoning_effort" not in payload

    def test_max_clamps_to_high(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            payload = providers.build_request_payload("doubao-seed-2.0", "max", self._base())
        assert payload["reasoning_effort"] == "high"


class TestBuildPayloadDoubao:
    def _base(self) -> dict:
        return {"model": "doubao-pro-256k", "messages": []}

    def test_disabled_is_na(self) -> None:
        payload = providers.build_request_payload("doubao-pro-256k", "disabled", self._base())
        assert "reasoning_effort" not in payload
        assert "extra_body" not in payload

    def test_effort_dropped(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            payload = providers.build_request_payload("doubao-pro-256k", "high", self._base())
        assert "reasoning_effort" not in payload


class TestDescribeFromPayload:
    def test_deepseek_disabled(self) -> None:
        payload = {"model": "deepseek-v4-flash", "thinking": {"type": "disabled"}}
        desc = providers.describe_from_payload(payload)
        assert desc["provider"] == "deepseek"
        assert desc["thinking_mode"] == "disabled"
        assert desc["thinking_source"] == "thinking"

    def test_deepseek_enabled_with_effort_reports_effort(self) -> None:
        payload = {
            "model": "deepseek-v4-flash",
            "thinking": {"type": "enabled"},
            "reasoning_effort": "high",
        }
        desc = providers.describe_from_payload(payload)
        assert desc["thinking_mode"] == "high"
        assert desc["thinking_source"] == "reasoning_effort"

    def test_reasoning_effort_set(self) -> None:
        payload = {"model": "gpt-5", "reasoning_effort": "high"}
        desc = providers.describe_from_payload(payload)
        assert desc["thinking_mode"] == "high"
        assert desc["thinking_source"] == "reasoning_effort"

    def test_no_thinking_shows_default(self) -> None:
        payload = {"model": "deepseek-v4-flash"}
        desc = providers.describe_from_payload(payload)
        assert "default" in desc["thinking_mode"]

    def test_gpt4_shows_na(self) -> None:
        payload = {"model": "gpt-4o"}
        desc = providers.describe_from_payload(payload)
        assert desc["thinking_mode"] == "n/a"


class TestJsonMode:
    """Every provider family must declare json_mode: json_schema or json_object."""

    @pytest.mark.parametrize(
        "model,expected_mode",
        [
            ("gpt-5", "json_schema"),
            ("gpt-5-mini", "json_schema"),
            ("gemini-2.5-flash", "json_schema"),
            ("gemini-3-flash", "json_schema"),
            ("doubao-pro-256k", "json_schema"),
            ("doubao-seed-2.0", "json_schema"),
            ("o3-mini", "json_schema"),
            ("o4-mini", "json_schema"),
            ("gpt-4o", "json_object"),
            ("gpt-4.1-mini", "json_object"),
            ("gemini-pro", "json_object"),
            ("deepseek-v4-flash", "json_object"),
        ],
    )
    def test_json_mode_per_provider(self, model: str, expected_mode: str) -> None:
        family = providers.detect_provider(model)
        caps = providers.get_provider_caps(family)
        assert caps["json_mode"] == expected_mode

    def test_all_families_have_json_mode(self) -> None:
        for family, caps in providers._FAMILY_CAPABILITIES.items():
            assert "json_mode" in caps, f"{family} missing json_mode"
            assert caps["json_mode"] in ("json_schema", "json_object"), (
                f"{family} has invalid json_mode: {caps['json_mode']}"
            )

    def test_default_caps_has_json_mode(self) -> None:
        caps = providers._DEFAULT_CAPS
        assert "json_mode" in caps
        assert caps["json_mode"] in ("json_schema", "json_object")


class TestDeepMerge:
    def test_basic_merge(self) -> None:
        base = {"a": 1, "b": 2}
        overlay = {"b": 3, "c": 4}
        result = providers.deep_merge_payload(base, overlay)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self) -> None:
        base = {"extra_body": {"foo": "bar"}}
        overlay = {"extra_body": {"thinking": {"type": "disabled"}}}
        result = providers.deep_merge_payload(base, overlay)
        assert result["extra_body"]["foo"] == "bar"
        assert result["extra_body"]["thinking"]["type"] == "disabled"

    def test_does_not_mutate_inputs(self) -> None:
        base = {"a": {"b": 1}}
        overlay = {"a": {"c": 2}}
        result = providers.deep_merge_payload(base, overlay)
        assert "c" not in base["a"]
        assert result["a"] == {"b": 1, "c": 2}


class TestThinkingModeMatrix:
    @pytest.mark.parametrize(
        "model,effort,expected_fields,expected_mode",
        [
            (
                "deepseek-v4-flash",
                "disabled",
                {"thinking": {"type": "disabled"}},
                "disabled",
            ),
            ("deepseek-v4-flash", "high", {"reasoning_effort": "high"}, "high"),
            ("deepseek-v4-flash", None, {}, "default(deepseek)"),
            ("gemini-2.5-flash", "disabled", {"reasoning_effort": "none"}, "disabled"),
            ("gemini-3-flash", "disabled", {"reasoning_effort": "minimal"}, "minimal"),
            ("gpt-5", "disabled", {"reasoning_effort": "minimal"}, "minimal"),
            ("doubao-seed-2.0", "disabled", {"reasoning_effort": "minimal"}, "minimal"),
            ("o3-mini", "disabled", {}, "default(openai_o)"),
            ("gpt-4o", "disabled", {}, "n/a"),
            ("doubao-pro-256k", "disabled", {}, "n/a"),
        ],
    )
    def test_thinking_mode_matches_wire_fields(
        self,
        model: str,
        effort: str | None,
        expected_fields: dict[str, object],
        expected_mode: str,
    ) -> None:
        base = {"model": model, "messages": []}
        payload = providers.build_request_payload(model, effort, base)
        wire_fields = {
            key: value for key, value in payload.items() if key not in {"model", "messages"}
        }

        assert wire_fields == expected_fields
        assert "extra_body" not in payload
        assert providers.describe_from_payload(payload)["thinking_mode"] == expected_mode
