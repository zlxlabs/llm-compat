from __future__ import annotations

# ruff: noqa: E402, I001
import json
import logging
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llm_compat import providers
from scripts.export_conformance import build_conformance_document, classify_warning


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
