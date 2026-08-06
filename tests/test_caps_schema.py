from __future__ import annotations

from typing import Any

import pytest

from llm_compat import providers
from llm_compat.caps_schema import REQUIRED_CAPS_KEYS, validate_family_caps


@pytest.fixture
def valid_caps() -> dict[str, Any]:
    return {
        "disable_mode": "native",
        "efforts": frozenset({"low", "high"}),
        "supports_vision": False,
        "json_mode": "json_object",
    }


@pytest.mark.parametrize("missing_key", sorted(REQUIRED_CAPS_KEYS))
def test_missing_required_key_is_named(
    valid_caps: dict[str, Any], missing_key: str
) -> None:
    valid_caps.pop(missing_key)

    with pytest.raises(ValueError, match=missing_key):
        validate_family_caps("example", valid_caps)


def test_invalid_disable_mode_is_rejected(valid_caps: dict[str, Any]) -> None:
    valid_caps["disable_mode"] = "invalid"

    with pytest.raises(ValueError, match="disable_mode"):
        validate_family_caps("example", valid_caps)


def test_invalid_json_mode_is_rejected(valid_caps: dict[str, Any]) -> None:
    valid_caps["json_mode"] = "invalid"

    with pytest.raises(ValueError, match="json_mode"):
        validate_family_caps("example", valid_caps)


def test_non_bool_vision_is_rejected(valid_caps: dict[str, Any]) -> None:
    valid_caps["supports_vision"] = 1

    with pytest.raises(ValueError, match="supports_vision"):
        validate_family_caps("example", valid_caps)


def test_unranked_effort_is_rejected(valid_caps: dict[str, Any]) -> None:
    valid_caps["efforts"] = {"low", "ultra"}

    with pytest.raises(ValueError, match="ultra"):
        validate_family_caps("example", valid_caps)


def test_complete_caps_are_accepted(valid_caps: dict[str, Any]) -> None:
    validate_family_caps("example", valid_caps)


def test_schema_uses_provider_effort_rank(valid_caps: dict[str, Any]) -> None:
    valid_caps["efforts"] = {next(iter(providers._EFFORT_RANK))}

    validate_family_caps("example", valid_caps)
