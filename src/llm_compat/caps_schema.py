"""Lightweight validation for provider capability records."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

REQUIRED_CAPS_KEYS: frozenset[str] = frozenset(
    {"disable_mode", "efforts", "supports_vision", "json_mode"}
)
VALID_DISABLE_MODES: tuple[str, ...] = (
    "native",
    "effort_none",
    "minimal_fallback",
    "unsupported",
    "na",
)
VALID_JSON_MODES: tuple[str, ...] = ("json_object", "json_schema")
DISABLE_MODE_SEMANTICS: dict[str, str] = {
    "native": '发 {"thinking": {"type": "disabled"}}',
    "effort_none": '发 {"reasoning_effort": "none"}',
    "minimal_fallback": '发 {"reasoning_effort": "minimal"}',
    "unsupported": "关不掉，丢弃意图并告警",
    "na": "该族本来就不推理，什么都不发",
}
def validate_family_caps(family: str, caps: dict[str, Any]) -> None:
    """Raise ``ValueError`` when a family capability record is invalid."""
    missing = REQUIRED_CAPS_KEYS.difference(caps)
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"Provider family {family!r} is missing required caps keys: {names}")

    disable_mode = caps["disable_mode"]
    if not isinstance(disable_mode, str) or disable_mode not in VALID_DISABLE_MODES:
        raise ValueError(f"Provider family {family!r} has invalid disable_mode: {disable_mode!r}")

    json_mode = caps["json_mode"]
    if not isinstance(json_mode, str) or json_mode not in VALID_JSON_MODES:
        raise ValueError(f"Provider family {family!r} has invalid json_mode: {json_mode!r}")

    if type(caps["supports_vision"]) is not bool:
        raise ValueError(f"Provider family {family!r} has non-bool supports_vision")

    efforts = caps["efforts"]
    if isinstance(efforts, (str, bytes, Mapping)):
        raise ValueError(f"Provider family {family!r} efforts must be an iterable of strings")
    try:
        effort_values = tuple(efforts)
    except TypeError as error:
        raise ValueError(
            f"Provider family {family!r} efforts must be an iterable of strings"
        ) from error

    from .providers import _EFFORT_RANK

    invalid = sorted({repr(value) for value in effort_values if (
        not isinstance(value, str) or value not in _EFFORT_RANK
    )})
    if invalid:
        raise ValueError(
            f"Provider family {family!r} has unranked effort values: {', '.join(invalid)}"
        )
