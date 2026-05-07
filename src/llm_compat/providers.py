from __future__ import annotations

import fnmatch
import logging
from typing import Any

logger = logging.getLogger(__name__)

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

_FAMILY_CAPABILITIES: dict[str, dict[str, Any]] = {
    "deepseek": {
        "disable_mode": "native",
        "efforts": frozenset({"low", "medium", "high", "max", "xhigh"}),
        "min_effort": "low",
        "max_effort": "xhigh",
    },
    "gemini_25": {
        "disable_mode": "effort_none",
        "efforts": frozenset({"low", "medium", "high"}),
        "min_effort": "low",
        "max_effort": "high",
    },
    "gemini_3": {
        "disable_mode": "minimal_fallback",
        "efforts": frozenset({"minimal", "low", "medium", "high"}),
        "min_effort": "minimal",
        "max_effort": "high",
    },
    "gemini": {
        "disable_mode": "effort_none",
        "efforts": frozenset({"low", "medium", "high"}),
        "min_effort": "low",
        "max_effort": "high",
    },
    "openai_gpt5": {
        "disable_mode": "minimal_fallback",
        "efforts": frozenset({"minimal", "low", "medium", "high"}),
        "min_effort": "minimal",
        "max_effort": "high",
    },
    "openai_gpt4": {
        "disable_mode": "na",
        "efforts": frozenset(),
        "min_effort": None,
        "max_effort": None,
    },
    "openai_o": {
        "disable_mode": "unsupported",
        "efforts": frozenset({"low", "medium", "high"}),
        "min_effort": "low",
        "max_effort": "high",
    },
    "doubao_seed": {
        "disable_mode": "minimal_fallback",
        "efforts": frozenset({"minimal", "low", "medium", "high"}),
        "min_effort": "minimal",
        "max_effort": "high",
    },
    "doubao": {
        "disable_mode": "na",
        "efforts": frozenset(),
        "min_effort": None,
        "max_effort": None,
    },
    "openai": {
        "disable_mode": "na",
        "efforts": frozenset({"low", "medium", "high"}),
        "min_effort": "low",
        "max_effort": "high",
    },
}

_custom_patterns: tuple[tuple[str, str], ...] | None = None


def register_provider(
    pattern: str,
    family: str,
    *,
    caps: dict[str, Any] | None = None,
) -> None:
    global _custom_patterns
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
) -> str:
    if not model or not isinstance(model, str):
        logger.warning("detect_provider: invalid model name %r, defaulting to 'openai'", model)
        return "openai"
    patterns = _effective_patterns(custom_patterns)
    model_lower = model.lower()
    for pattern, family in patterns:
        if fnmatch.fnmatch(model_lower, pattern.lower()):
            return family
    logger.warning("detect_provider: unknown model %r, defaulting to 'openai'", model)
    return "openai"


def _translate(family: str, effort: str | None) -> dict[str, Any]:
    if effort is None:
        return {}

    caps = _FAMILY_CAPABILITIES.get(family, _FAMILY_CAPABILITIES["openai"])

    if effort == "disabled":
        mode = caps["disable_mode"]
        if mode == "native":
            return {"extra_body": {"thinking": {"type": "disabled"}}}
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

    accepted = caps["efforts"]
    if not accepted:
        logger.warning(
            "Provider family %r does not support reasoning_effort; dropping %r.",
            family,
            effort,
        )
        return {}

    if effort in accepted:
        return {"reasoning_effort": effort}

    requested_rank = _EFFORT_RANK.get(effort, _EFFORT_RANK["high"])
    min_effort = caps.get("min_effort")
    max_effort = caps.get("max_effort")
    if min_effort and requested_rank < _EFFORT_RANK[min_effort]:
        clamped = min_effort
    elif max_effort:
        clamped = max_effort
    else:
        clamped = next(iter(sorted(accepted))) if accepted else None
    if clamped is None:
        logger.warning("Provider family %r has no accepted effort; dropping %r.", family, effort)
        return {}
    logger.warning(
        "Provider family %r does not accept effort %r, clamping to %r.",
        family,
        effort,
        clamped,
    )
    return {"reasoning_effort": clamped}


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def build_request_payload(
    model: str,
    reasoning_effort: str | None,
    base_payload: dict[str, Any],
    custom_patterns: tuple[tuple[str, str], ...] | None = None,
) -> dict[str, Any]:
    family = detect_provider(model, custom_patterns)
    translation = _translate(family, reasoning_effort)
    return _deep_merge(base_payload, translation)


def describe_from_payload(
    payload: dict[str, Any],
    custom_patterns: tuple[tuple[str, str], ...] | None = None,
) -> dict[str, str]:
    model = payload.get("model", "unknown") if isinstance(payload, dict) else "unknown"
    family = detect_provider(model, custom_patterns)
    caps = _FAMILY_CAPABILITIES.get(family, _FAMILY_CAPABILITIES["openai"])

    effort = payload.get("reasoning_effort") if isinstance(payload, dict) else None
    extra_body = payload.get("extra_body") if isinstance(payload, dict) else None
    thinking_type = None
    if isinstance(extra_body, dict):
        thinking_cfg = extra_body.get("thinking")
        if isinstance(thinking_cfg, dict):
            thinking_type = thinking_cfg.get("type")

    if thinking_type == "disabled":
        mode, source = "disabled", "extra_body.thinking"
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
