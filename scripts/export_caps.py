"""Export provider capability knowledge to the language-neutral caps.json format."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from llm_compat import caps_schema, providers  # noqa: E402


def _serialize_caps(family: str, caps: dict[str, Any]) -> dict[str, Any]:
    caps_schema.validate_family_caps(family, caps)
    efforts = sorted(caps["efforts"], key=providers._EFFORT_RANK.__getitem__)
    return {
        "disable_mode": caps["disable_mode"],
        "efforts": efforts,
        "supports_vision": caps["supports_vision"],
        "json_mode": caps["json_mode"],
    }


def _serialize_default_caps(caps: dict[str, Any], note: str) -> dict[str, Any]:
    serialized = _serialize_caps("default", caps)
    serialized["note"] = note
    return serialized


def build_caps_document() -> dict[str, Any]:
    """Build a deterministic JSON-compatible document from provider constants."""
    effort_rank = dict(
        sorted(providers._EFFORT_RANK.items(), key=lambda item: item[1])
    )
    patterns = [
        {"pattern": pattern, "family": family}
        for pattern, family in providers._DEFAULT_PROVIDER_PATTERNS
    ]
    families = {
        family: _serialize_caps(family, providers._FAMILY_CAPABILITIES[family])
        for family in sorted(providers._FAMILY_CAPABILITIES)
    }
    schema = {
        "required_keys": sorted(caps_schema.REQUIRED_CAPS_KEYS),
        "field_types": {
            "disable_mode": "string",
            "efforts": "array<string>",
            "supports_vision": "boolean",
            "json_mode": "string",
        },
        "efforts_allowed_values": list(effort_rank),
    }
    return {
        "schema_version": 1,
        "generated_from": "src/llm_compat/providers.py",
        "generated_by": "scripts/export_caps.py",
        "gateway": {
            "kind": "new-api",
            "note": (
                "这些能力值记录的是通过 New API 代理观测到的网关行为，"
                "不是模型固有属性。换网关需重新验证。"
            ),
        },
        "matching": {
            "algorithm": "fnmatch",
            "case_sensitive": False,
            "order": "first-match-wins",
            "note": (
                "模型名与 pattern 均先 lowercase，再按 patterns 数组顺序做 fnmatch，"
                "首个命中即返回。未命中时 family=openai 且 matched=false。"
            ),
        },
        "effort_rank": effort_rank,
        "enums": {
            "disable_mode": list(caps_schema.VALID_DISABLE_MODES),
            "json_mode": list(caps_schema.VALID_JSON_MODES),
        },
        "disable_mode_semantics": {
            mode: caps_schema.DISABLE_MODE_SEMANTICS[mode]
            for mode in caps_schema.VALID_DISABLE_MODES
        },
        "schema": schema,
        "default_caps": _serialize_default_caps(
            providers._DEFAULT_CAPS,
            "未知 family 的兜底能力；用于完全未知的 provider family。",
        ),
        "partial_caps_defaults": _serialize_default_caps(
            providers._PARTIAL_CAPS_DEFAULTS,
            "不完整自定义 caps 的字段兜底；用于 register_provider 传入部分能力时的兼容路径。",
        ),
        "patterns": patterns,
        "families": families,
    }


def export_caps(output: Path = ROOT / "caps.json") -> None:
    """Write the deterministic capability document to ``output``."""
    output.write_text(
        json.dumps(build_caps_document(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "caps.json",
        help="output path (default: repository root caps.json)",
    )
    args = parser.parse_args()
    export_caps(args.output)


if __name__ == "__main__":
    main()
