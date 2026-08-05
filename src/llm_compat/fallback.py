from __future__ import annotations

import fnmatch

from .providers import detect_provider, get_provider_caps


def resolve_fallback_chain(
    model: str,
    config: dict[str, list[str]] | None,
) -> list[str] | None:
    if not config:
        return None
    model_lower = model.lower()
    for pattern, chain in config.items():
        if fnmatch.fnmatch(model_lower, pattern.lower()):
            return list(chain)
    return None


def filter_by_modality(
    chain: list[str],
    *,
    needs_vision: bool,
) -> list[str]:
    if not needs_vision:
        return chain
    result = []
    for model in chain:
        detection = detect_provider(model)
        assert detection.family is not None
        family = detection.family
        caps = get_provider_caps(family)
        if caps["supports_vision"]:
            result.append(model)
    return result
