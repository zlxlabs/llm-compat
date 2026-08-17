"""Independent, hand-written provider capability contract snapshot.

Task-Id llm-compat-20260806-F2: this snapshot must remain a hand-written
second source. R2 reproduced that changing ``doubao_seed.supports_vision``
from ``False`` to ``True`` made the drift test fail before export, but made
all 1004 tests pass after re-running both exporters; ``conformance.json``
still reported ``reviewed: true``. Deriving this data from
``_FAMILY_CAPABILITIES`` or ``caps.json`` would recreate that blind spot.
"""
from __future__ import annotations

import pytest

from llm_compat import providers

# Do not replace these literals with an import, exporter call, or JSON load.
# The values are a deliberately independent oracle for the cross-language
# contract consumed by Bun/Go implementations and 10+ Python projects.
_EXPECTED_FAMILY_CAPABILITIES = {
    "deepseek": {
        "disable_mode": "native",
        "efforts": frozenset({"low", "high", "max", "xhigh"}),
        "supports_vision": False,
        "json_mode": "json_object",
    },
    "gemini_25": {
        "disable_mode": "effort_none",
        "efforts": frozenset({"low", "medium", "high"}),
        "supports_vision": True,
        "json_mode": "json_schema",
    },
    "gemini_3": {
        "disable_mode": "minimal_fallback",
        "efforts": frozenset({"minimal", "low", "medium", "high"}),
        "supports_vision": True,
        "json_mode": "json_schema",
    },
    "gemini": {
        "disable_mode": "effort_none",
        "efforts": frozenset({"low", "medium", "high"}),
        "supports_vision": True,
        "json_mode": "json_object",
    },
    "openai_gpt5": {
        "disable_mode": "minimal_fallback",
        "efforts": frozenset({"minimal", "low", "medium", "high"}),
        "supports_vision": True,
        "json_mode": "json_schema",
    },
    "openai_gpt4": {
        "disable_mode": "na",
        "efforts": frozenset(),
        "supports_vision": True,
        "json_mode": "json_object",
    },
    "openai_o": {
        "disable_mode": "unsupported",
        "efforts": frozenset({"low", "medium", "high"}),
        "supports_vision": False,
        "json_mode": "json_schema",
    },
    "doubao_seed": {
        "disable_mode": "minimal_fallback",
        "efforts": frozenset({"minimal", "low", "medium", "high"}),
        "supports_vision": False,
        "json_mode": "json_schema",
    },
    "doubao": {
        "disable_mode": "na",
        "efforts": frozenset(),
        "supports_vision": False,
        "json_mode": "json_schema",
    },
    "mimo": {
        "disable_mode": "na",
        "efforts": frozenset({"low", "medium", "high"}),
        "supports_vision": False,
        "json_mode": "json_object",
    },
    "openai": {
        "disable_mode": "na",
        "efforts": frozenset({"low", "medium", "high"}),
        "supports_vision": True,
        "json_mode": "json_schema",
    },
}

_EXPECTED_DEFAULT_NON_JSON_CAPS = {
    "disable_mode": "na",
    "efforts": frozenset(),
    "supports_vision": True,
}

_CAPABILITY_VALUE_CHANGE_MESSAGE = (
    "能力表值与手写快照不一致。你正在修改跨语言契约值，Bun/Go 实现和 10+ Python 项目"
    "都依赖它。请先用 scripts/probe_caps.py 探针实测、供应商文档或真实调用记录举证；"
    "确认无误后同步修改本测试中的手写快照，并在 commit message 中写明证据来源。"
)


def test_family_capabilities_match_handwritten_contract_snapshot() -> None:
    assert set(providers._FAMILY_CAPABILITIES) == set(_EXPECTED_FAMILY_CAPABILITIES), (
        _CAPABILITY_VALUE_CHANGE_MESSAGE
    )

    for family, expected_caps in _EXPECTED_FAMILY_CAPABILITIES.items():
        assert providers._FAMILY_CAPABILITIES[family] == expected_caps, (
            f"family={family!r}: {_CAPABILITY_VALUE_CHANGE_MESSAGE}"
        )


@pytest.mark.parametrize(
    "caps_name",
    ("_DEFAULT_CAPS", "_PARTIAL_CAPS_DEFAULTS"),
)
def test_default_caps_non_json_fields_match_handwritten_snapshot(
    caps_name: str,
) -> None:
    actual_caps = getattr(providers, caps_name)
    actual_non_json_caps = {
        field: actual_caps.get(field)
        for field in _EXPECTED_DEFAULT_NON_JSON_CAPS
    }

    assert actual_non_json_caps == _EXPECTED_DEFAULT_NON_JSON_CAPS, (
        f"{caps_name}: {_CAPABILITY_VALUE_CHANGE_MESSAGE}"
    )
