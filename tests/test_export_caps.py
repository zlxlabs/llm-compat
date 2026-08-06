from __future__ import annotations

# ruff: noqa: E402
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CAPS_PATH = ROOT / "caps.json"
EXPORT_SCRIPT = ROOT / "scripts" / "export_caps.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llm_compat import providers
from llm_compat.caps_schema import (
    REQUIRED_CAPS_KEYS,
    VALID_DISABLE_MODES,
    VALID_JSON_MODES,
    validate_family_caps,
)
from scripts.export_caps import build_caps_document


def _load_caps() -> dict[str, Any]:
    return json.loads(CAPS_PATH.read_text(encoding="utf-8"))


def test_export_is_byte_deterministic(tmp_path: Path) -> None:
    outputs = [tmp_path / "caps-0.json", tmp_path / "caps-1.json"]
    for seed, output in enumerate(outputs):
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = str(seed)
        subprocess.run(
            [sys.executable, str(EXPORT_SCRIPT), "--output", str(output)],
            cwd=ROOT,
            env=env,
            check=True,
        )

    assert outputs[0].read_bytes() == outputs[1].read_bytes()


def test_export_contains_exactly_all_provider_families() -> None:
    document = _load_caps()

    assert set(document["families"]) == set(providers._FAMILY_CAPABILITIES)


def test_patterns_preserve_source_order() -> None:
    document = build_caps_document()
    expected = [
        {"pattern": pattern, "family": family}
        for pattern, family in providers._DEFAULT_PROVIDER_PATTERNS
    ]

    assert document["patterns"] == expected


def test_every_family_efforts_are_rank_sorted() -> None:
    document = _load_caps()
    rank = providers._EFFORT_RANK

    for caps in document["families"].values():
        efforts = caps["efforts"]
        assert efforts == sorted(efforts, key=rank.__getitem__)


def test_defaults_keep_distinct_json_modes_and_explain_why() -> None:
    document = build_caps_document()
    default_caps = document["default_caps"]
    partial_caps_defaults = document["partial_caps_defaults"]

    assert default_caps["json_mode"] == "json_schema"
    assert partial_caps_defaults["json_mode"] == "json_object"
    assert default_caps["note"]
    assert partial_caps_defaults["note"]


def test_declared_schema_uses_validator_constants() -> None:
    document = build_caps_document()

    assert document["schema"]["required_keys"] == sorted(REQUIRED_CAPS_KEYS)
    assert document["enums"]["disable_mode"] == list(VALID_DISABLE_MODES)
    assert document["enums"]["json_mode"] == list(VALID_JSON_MODES)


def test_exported_family_caps_pass_schema_validation() -> None:
    document = build_caps_document()

    for family, caps in document["families"].items():
        validate_family_caps(family, caps)


def test_checked_in_caps_matches_export() -> None:
    expected = json.dumps(build_caps_document(), ensure_ascii=False, indent=2) + "\n"

    assert CAPS_PATH.read_text(encoding="utf-8") == expected
