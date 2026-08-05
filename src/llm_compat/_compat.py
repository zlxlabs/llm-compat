from __future__ import annotations

from .providers import (
    detect_provider,
    get_provider_caps,
    normalize_reasoning_effort,
    resolve_effort_clamp,
)

__all__ = ["normalize_reasoning_effort", "validate_config", "validate_fallback_config"]


def validate_config(model: str, effort: str | None) -> list[str]:
    effort = normalize_reasoning_effort(effort)
    if effort is None:
        return []

    warnings: list[str] = []
    family = detect_provider(model)
    caps = get_provider_caps(family)

    if effort == "disabled":
        mode = caps["disable_mode"]
        if mode == "unsupported":
            warnings.append(
                f"Provider '{family}' (model '{model}') cannot disable thinking; "
                f"'disabled' will be dropped at runtime."
            )
        return warnings

    clamped = resolve_effort_clamp(family, effort)
    if clamped is None:
        warnings.append(
            f"Provider '{family}' (model '{model}') does not support reasoning_effort; "
            f"value '{effort}' will be dropped at runtime."
        )
        return warnings

    if clamped != effort:
        warnings.append(
            f"Provider '{family}' (model '{model}') does not accept effort '{effort}'; "
            f"will clamp to '{clamped}' at runtime."
        )

    return warnings


def validate_fallback_config(
    config: dict[str, list[str]] | None,
) -> list[str]:
    if not config:
        return []

    warnings: list[str] = []
    for pattern, chain in config.items():
        primary_family = detect_provider(pattern.replace("*", "x").replace("?", "x"))
        primary_caps = get_provider_caps(primary_family)
        primary_has_vision = primary_caps.get("supports_vision", True)
        any_vision_fb = False

        for fb_model in chain:
            fb_family = detect_provider(fb_model)
            fb_caps = get_provider_caps(fb_family)
            if fb_caps.get("supports_vision", True):
                any_vision_fb = True

        if primary_has_vision and not any_vision_fb and chain:
            warnings.append(
                f"Pattern '{pattern}' supports vision but no fallback model supports vision; "
                f"vision requests will fail on fallback."
            )

    return warnings
