from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass
from typing import Any, cast

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderDetection:
    """Provider family plus whether the model/pattern matched a known family.

    Migration: use ``.family`` where older versions returned the provider string
    directly.
    """

    family: str
    matched: bool

_DEFAULT_PROVIDER_PATTERNS: tuple[tuple[str, str], ...] = (
    ("deepseek-chat", "deepseek"),
    ("deepseek-reasoner", "deepseek"),
    ("deepseek-*", "deepseek"),
    ("gemini-2.5-*", "gemini_25"),
    ("gemini-3-*", "gemini_3"),
    ("gemini-3.*-*", "gemini_3"),
    ("gemini-*", "gemini"),
    ("gpt-5", "openai_gpt5"),
    ("gpt-5-*", "openai_gpt5"),
    ("gpt-5.*", "openai_gpt5"),
    ("gpt-4*", "openai_gpt4"),
    ("gpt-*", "openai"),
    # Doubao（doubao-seed 系列支持 thinking，普通 doubao 不支持）
    ("doubao-seed-*", "doubao_seed"),
    ("doubao-*", "doubao"),
    # OpenAI o-series
    ("o1*", "openai_o"),
    ("o3*", "openai_o"),
    ("o4*", "openai_o"),
    ("o5*", "openai_o"),
)

_EFFORT_RANK: dict[str, int] = {
    "minimal": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "max": 4,
    "xhigh": 5,
}


def normalize_reasoning_effort(value: str | None) -> str | None:
    """Normalize caller-provided reasoning effort values to canonical values."""
    if value is None:
        return None
    if not isinstance(value, str):
        return None

    normalized = value.strip().casefold()
    if not normalized:
        return None
    if normalized in {"none", "off", "false"}:
        logger.warning(
            "reasoning_effort alias %r is deprecated, use 'disabled' instead. "
            "Auto-converting to 'disabled'.",
            value,
        )
        return "disabled"
    return normalized


_FAMILY_CAPABILITIES: dict[str, dict[str, Any]] = {
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
    "openai": {
        "disable_mode": "na",
        "efforts": frozenset({"low", "medium", "high"}),
        "supports_vision": True,
        "json_mode": "json_schema",
    },
}

_DEFAULT_CAPS: dict[str, Any] = {
    "disable_mode": "na",
    "efforts": frozenset(),
    "supports_vision": True,
    "json_mode": "json_schema",
}


def get_provider_caps(family: str | ProviderDetection | None) -> dict[str, Any]:
    family_name = family.family if isinstance(family, ProviderDetection) else family
    if family_name is None:
        return dict(_DEFAULT_CAPS)
    caps = _FAMILY_CAPABILITIES.get(family_name)
    if caps is None:
        return dict(_DEFAULT_CAPS)
    return {**_DEFAULT_CAPS, **caps}


def _validate_ranked_efforts(family: str, caps: dict[str, Any]) -> None:
    efforts = caps.get("efforts", ())
    invalid_efforts = [
        effort
        for effort in efforts
        if not isinstance(effort, str) or effort not in _EFFORT_RANK
    ]
    if invalid_efforts:
        invalid_values = ", ".join(sorted(repr(value) for value in invalid_efforts))
        raise ValueError(
            f"Provider family {family!r} has unranked effort values: {invalid_values}. "
            "Custom caps['efforts'] must contain only ranked effort values."
        )


_custom_patterns: tuple[tuple[str, str], ...] | None = None


def register_provider(
    pattern: str,
    family: str,
    *,
    caps: dict[str, Any] | None = None,
) -> None:
    global _custom_patterns
    if caps is not None:
        _validate_ranked_efforts(family, caps)

    entry = (pattern, family)
    if _custom_patterns:
        _custom_patterns = (entry,) + _custom_patterns
    else:
        _custom_patterns = (entry,)
    if caps is not None:
        _FAMILY_CAPABILITIES[family] = caps


def set_custom_patterns(patterns: Any) -> None:
    global _custom_patterns
    if not patterns:
        _custom_patterns = None
        return
    if isinstance(patterns, dict):
        pairs = list(patterns.items())
    elif isinstance(patterns, (list, tuple)):
        pairs = []
        for entry in patterns:
            if isinstance(entry, (list, tuple)) and len(entry) == 2:
                pairs.append((entry[0], entry[1]))
    else:
        logger.warning("Invalid provider_patterns type %s, ignoring", type(patterns).__name__)
        _custom_patterns = None
        return
    normalized: list[tuple[str, str]] = []
    for pattern, family in pairs:
        if isinstance(pattern, str) and isinstance(family, str):
            normalized.append((pattern, family))
    _custom_patterns = tuple(normalized) if normalized else None


def _effective_patterns(
    custom: tuple[tuple[str, str], ...] | None = None,
) -> tuple[tuple[str, str], ...]:
    if custom:
        return tuple(custom) + _DEFAULT_PROVIDER_PATTERNS
    if _custom_patterns:
        return _custom_patterns + _DEFAULT_PROVIDER_PATTERNS
    return _DEFAULT_PROVIDER_PATTERNS


def detect_provider(
    model: str | None,
    custom_patterns: tuple[tuple[str, str], ...] | None = None,
) -> ProviderDetection:
    if not model or not isinstance(model, str):
        logger.warning("detect_provider: invalid model name %r, defaulting to 'openai'", model)
        return ProviderDetection(family="openai", matched=False)
    patterns = _effective_patterns(custom_patterns)
    model_lower = model.lower()
    for pattern, family in patterns:
        if fnmatch.fnmatch(model_lower, pattern.lower()):
            return ProviderDetection(family=family, matched=True)
    logger.warning("detect_provider: unknown model %r, defaulting to 'openai'", model)
    return ProviderDetection(family="openai", matched=False)


def detect_provider_for_pattern(
    pattern: str,
    custom_patterns: tuple[tuple[str, str], ...] | None = None,
) -> ProviderDetection | None:
    """Resolve a fallback pattern without fabricating a model name."""
    if not pattern or not isinstance(pattern, str):
        return None

    pattern_lower = pattern.lower()
    for known_pattern, family in _effective_patterns(custom_patterns):
        if fnmatch.fnmatch(pattern_lower, known_pattern.lower()):
            return ProviderDetection(family=family, matched=True)
    return None


def _effort_rank(effort: str) -> int:
    return _EFFORT_RANK.get(effort, _EFFORT_RANK["high"])


def resolve_effort_clamp(family: str, effort: str) -> str | None:
    """Resolve an effort to the nearest value supported by a provider family."""
    caps = _FAMILY_CAPABILITIES.get(family, _FAMILY_CAPABILITIES["openai"])
    accepted = cast(frozenset[str], caps["efforts"])
    if not accepted:
        return None
    if effort in accepted:
        return effort

    requested_rank = _effort_rank(effort)
    ranked_efforts = sorted(accepted, key=_effort_rank)
    return next(
        (
            accepted_effort
            for accepted_effort in ranked_efforts
            if _effort_rank(accepted_effort) >= requested_rank
        ),
        ranked_efforts[-1],
    )


# _translate decision tree:
#
#   effort=None ────────────────────────────────────────────────> {}
#   effort=disabled
#     ├─ native             ────────────────────────────────────> thinking=disabled
#     ├─ effort_none        ────────────────────────────────────> reasoning_effort=none
#     ├─ minimal_fallback   ────────────────────────────────────> reasoning_effort=minimal
#     ├─ unsupported        ────────────────────────────────────> warn + {}
#     └─ na                 ─────────────────────────────────────> {}
#   other effort ───────────────> resolve_effort_clamp
#     ├─ accepted as-is ────────────────────────────────────────> reasoning_effort=requested
#     ├─ nearest supported rank >= requested ───────────────────> reasoning_effort=clamped
#     ├─ no higher rank ─────────────────────────────────────────> highest supported rank
#     └─ no supported efforts ──────────────────────────────────> warn + {}
def _translate(family: str, effort: str | None) -> dict[str, Any]:
    if effort is None:
        return {}

    caps = _FAMILY_CAPABILITIES.get(family, _FAMILY_CAPABILITIES["openai"])

    if effort == "disabled":
        mode = caps["disable_mode"]
        if mode == "native":
            return {"thinking": {"type": "disabled"}}
        if mode == "effort_none":
            return {"reasoning_effort": "none"}
        if mode == "minimal_fallback":
            return {"reasoning_effort": "minimal"}
        if mode == "unsupported":
            logger.warning(
                "Provider family %r cannot disable thinking; dropping 'disabled' intent.",
                family,
            )
            return {}
        return {}

    clamped = resolve_effort_clamp(family, effort)
    if clamped is None:
        logger.warning(
            "Provider %r effort unsupported: request=%r -> actual=dropped; direction=drop.",
            family,
            effort,
        )
        return {}

    if clamped == effort:
        return {"reasoning_effort": effort}

    requested_rank = _effort_rank(effort)
    actual_rank = _effort_rank(clamped)
    direction = "upward" if actual_rank > requested_rank else "downward"
    logger.warning(
        "Provider family %r effort clamp: requested=%r -> actual=%r; direction=%s.",
        family,
        effort,
        clamped,
        direction,
    )
    return {"reasoning_effort": clamped}


def deep_merge_payload(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge_payload(result[key], value)
        else:
            result[key] = value
    return result


def build_request_payload(
    model: str,
    reasoning_effort: str | None,
    base_payload: dict[str, Any],
    custom_patterns: tuple[tuple[str, str], ...] | None = None,
) -> dict[str, Any]:
    reasoning_effort = normalize_reasoning_effort(reasoning_effort)
    detection = detect_provider(model, custom_patterns)
    family = detection.family
    translation = _translate(family, reasoning_effort)
    return deep_merge_payload(base_payload, translation)


def describe_from_payload(
    payload: dict[str, Any],
    custom_patterns: tuple[tuple[str, str], ...] | None = None,
) -> dict[str, str]:
    model = payload.get("model", "unknown") if isinstance(payload, dict) else "unknown"
    detection = detect_provider(model, custom_patterns)
    family = detection.family
    caps = _FAMILY_CAPABILITIES.get(family, _FAMILY_CAPABILITIES["openai"])

    effort = payload.get("reasoning_effort") if isinstance(payload, dict) else None
    thinking = payload.get("thinking") if isinstance(payload, dict) else None
    thinking_type = None
    if isinstance(thinking, dict):
        thinking_type = thinking.get("type")

    if thinking_type == "disabled":
        mode, source = "disabled", "thinking"
    elif effort == "none":
        mode, source = "disabled", "reasoning_effort=none"
    elif effort:
        mode, source = str(effort), "reasoning_effort"
    elif caps["disable_mode"] == "na":
        mode, source = "n/a", "model_default"
    else:
        mode, source = f"default({family})", "model_default"

    return {
        "provider": family,
        "model": str(model),
        "thinking_mode": mode,
        "thinking_source": source,
    }
