from __future__ import annotations

import logging

from .providers import _EFFORT_RANK, _FAMILY_CAPABILITIES, detect_provider

logger = logging.getLogger(__name__)


def normalize_reasoning_effort(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if stripped == "none":
        logger.warning(
            "reasoning_effort='none' is deprecated, use 'disabled' instead. "
            "Auto-converting to 'disabled'."
        )
        return "disabled"
    return stripped


def validate_config(model: str, effort: str | None) -> list[str]:
    if effort is None:
        return []

    warnings: list[str] = []
    family = detect_provider(model)
    caps = _FAMILY_CAPABILITIES.get(family, _FAMILY_CAPABILITIES["openai"])

    if effort == "disabled":
        mode = caps["disable_mode"]
        if mode == "unsupported":
            warnings.append(
                f"Provider '{family}' (model '{model}') cannot disable thinking; "
                f"'disabled' will be dropped at runtime."
            )
        return warnings

    accepted = caps["efforts"]
    if not accepted:
        warnings.append(
            f"Provider '{family}' (model '{model}') does not support reasoning_effort; "
            f"value '{effort}' will be dropped at runtime."
        )
        return warnings

    if effort not in accepted:
        max_effort = caps.get("max_effort")
        min_effort = caps.get("min_effort")
        rank = _EFFORT_RANK.get(effort, _EFFORT_RANK["high"])
        if min_effort and rank < _EFFORT_RANK.get(min_effort, 0):
            clamped = min_effort
        elif max_effort:
            clamped = max_effort
        else:
            clamped = "unknown"
        warnings.append(
            f"Provider '{family}' (model '{model}') does not accept effort '{effort}'; "
            f"will clamp to '{clamped}' at runtime."
        )

    return warnings
