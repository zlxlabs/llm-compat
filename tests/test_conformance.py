from __future__ import annotations

# ruff: noqa: E402, I001
import json
import logging
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llm_compat import providers
from scripts.export_conformance import (
    REVIEWED_VECTORS_DIGEST,
    _vectors_digest,
    build_conformance_document,
    classify_warning,
)


CONFORMANCE_PATH = ROOT / "conformance.json"
CONFORMANCE_DOCUMENT = json.loads(CONFORMANCE_PATH.read_text(encoding="utf-8"))
VECTORS: list[dict[str, Any]] = CONFORMANCE_DOCUMENT["vectors"]


def _evaluate_vector(
    vector: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> dict[str, Any]:
    inputs = vector["input"]
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=providers.logger.name):
        normalized_effort = providers.normalize_reasoning_effort(
            inputs["reasoning_effort"]
        )
        detection = providers.detect_provider(inputs["model"])
        translated = providers._translate(
            detection,
            normalized_effort,
            strict=inputs["strict"],
            model=inputs["model"],
        )

    return {
        "family": detection.family,
        "matched": detection.matched,
        "set": translated,
        "warnings": [
            classify_warning(record.getMessage())
            for record in caplog.records
            if record.levelno >= logging.WARNING
        ],
    }


@pytest.mark.parametrize("vector", VECTORS, ids=lambda vector: vector["id"])
def test_conformance_vector_matches_provider(
    vector: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert _evaluate_vector(vector, caplog) == vector["expect"]


def test_checked_in_conformance_matches_export() -> None:
    expected = json.dumps(
        build_conformance_document(),
        ensure_ascii=False,
        indent=2,
    ) + "\n"

    assert CONFORMANCE_PATH.read_text(encoding="utf-8") == expected


def test_every_warning_category_is_covered() -> None:
    categories = CONFORMANCE_DOCUMENT["warning_categories"]
    covered = {
        warning
        for vector in VECTORS
        for warning in vector["expect"]["warnings"]
    }

    assert set(categories) <= covered


def test_every_provider_family_has_checked_in_conformance_coverage() -> None:
    provider_families = set(providers._FAMILY_CAPABILITIES)
    covered_families = {
        vector["expect"]["family"]
        for vector in VECTORS
    }
    missing_families = sorted(provider_families - covered_families)

    assert not missing_families, (
        "Missing checked-in conformance vector coverage for provider family: "
        f"{', '.join(missing_families)}. "
        "往 scripts/export_conformance.py 的 MODEL_CASES 加一个该 family 的代表模型。"
    )


def test_reviewed_vectors_digest_matches_checked_in_document() -> None:
    assert _vectors_digest(VECTORS) == REVIEWED_VECTORS_DIGEST
    assert CONFORMANCE_DOCUMENT["reviewed"] is True


def test_changing_one_vector_invalidates_reviewed_digest() -> None:
    changed_vectors = deepcopy(VECTORS)
    changed_vectors[0]["id"] += "-changed"

    assert _vectors_digest(changed_vectors) != REVIEWED_VECTORS_DIGEST


def test_vectors_digest_is_insensitive_to_dict_key_order() -> None:
    first = [{"outer": {"b": 2, "a": 1}, "value": "same"}]
    second = [{"value": "same", "outer": {"a": 1, "b": 2}}]

    assert _vectors_digest(first) == _vectors_digest(second)


# The first seven messages mirror the current providers.logger.warning calls.
# The final boundary samples deliberately contain "strict" while belonging
# to another known category. A broad strict matcher must not steal them.
_WARNING_CLASSIFICATION_CASES = (
    (
        "reasoning_effort alias 'none' is deprecated, use 'disabled' instead. "
        "Auto-converting to 'disabled'.",
        "deprecated_effort_alias",
    ),
    (
        "strict_unknown_models: model 'qwen-max' did not match any known provider "
        "family; dropping reasoning_effort 'high' instead of guessing openai semantics.",
        "strict_drop_effort",
    ),
    (
        "detect_provider: unknown model 'qwen-max', defaulting to 'openai'",
        "unknown_model",
    ),
    (
        "Provider family 'gemini_25' effort clamp: requested='minimal' -> "
        "actual='low'; direction=upward.",
        "effort_clamp_up",
    ),
    (
        "Provider family 'deepseek' effort clamp: requested='ultra' -> "
        "actual='high'; direction=downward.",
        "effort_clamp_down",
    ),
    (
        "Provider 'openai_gpt4' effort unsupported: request='high' -> "
        "actual=dropped; direction=drop.",
        "effort_dropped_unsupported",
    ),
    (
        "Provider family 'openai_o' cannot disable thinking; dropping 'disabled' intent.",
        "disable_unsupported",
    ),
    (
        "detect_provider: unknown model 'qwen-max', defaulting to 'openai' "
        "during strict mode audit",
        "unknown_model",
    ),
    (
        "Provider family 'deepseek' effort clamp: requested='ultra' -> "
        "actual='high'; direction=downward. strict mode audit",
        "effort_clamp_down",
    ),
    (
        "Provider family 'openai_o' cannot disable thinking; dropping 'disabled' intent. "
        "strict mode audit",
        "disable_unsupported",
    ),
)


@pytest.mark.parametrize(
    ("message", "expected"),
    _WARNING_CLASSIFICATION_CASES,
    ids=[expected for _, expected in _WARNING_CLASSIFICATION_CASES],
)
def test_classify_warning_uses_discriminating_messages(
    message: str,
    expected: str,
) -> None:
    assert classify_warning(message) == expected


@pytest.mark.parametrize(
    "message",
    (
        "strict_unknown_models: model 'qwen-max' did not match any known provider "
        "family; retaining reasoning_effort 'high'.",
        "new provider warning: strict validation changed the fallback behavior",
    ),
)
def test_classify_warning_rejects_unclassified_messages(message: str) -> None:
    with pytest.raises(ValueError, match="^Unclassified provider warning:"):
        classify_warning(message)
