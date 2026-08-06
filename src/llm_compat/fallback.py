from __future__ import annotations

import fnmatch
import logging

from .providers import detect_provider, get_provider_caps

logger = logging.getLogger(__name__)


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
    strict: bool = False,
) -> list[str]:
    if not needs_vision:
        return chain
    result = []
    for model in chain:
        detection = detect_provider(model)
        family = detection.family
        if strict and not detection.matched:
            logger.warning(
                "strict_unknown_models: model %r did not match any known provider family; "
                "dropping it from the vision fallback chain.",
                model,
            )
            continue
        caps = get_provider_caps(family)
        if caps["supports_vision"]:
            result.append(model)
    return result
