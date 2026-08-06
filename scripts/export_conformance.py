"""Export provider translation behavior to the language-neutral conformance format."""
# ruff: noqa: E402, I001
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from llm_compat import providers  # noqa: E402


MODEL_CASES: tuple[str, ...] = (
    "deepseek-chat",
    "gemini-2.5-pro",
    "gemini-3-pro",
    "gemini-1.5-pro",
    "gpt-5",
    "gpt-4o",
    "o3-mini",
    "doubao-seed-1.6",
    "doubao-pro",
    "gpt-3.5-turbo",
    "qwen-max",
    "DeepSeek-Chat",
)

EFFORT_CASES: tuple[str | None, ...] = (
    None,
    "minimal",
    "low",
    "medium",
    "high",
    "max",
    "xhigh",
    "disabled",
    "none",
    "off",
    "false",
    "",
    "  ",
    "HIGH",
)

WARNING_CATEGORIES: dict[str, str] = {
    "deprecated_effort_alias": (
        "reasoning_effort 使用已废弃别名（none/off/false），已归一化为 disabled"
    ),
    "disable_unsupported": "provider family 不支持关闭思考，disabled 意图被丢弃",
    "effort_clamp_down": "reasoning_effort 被夹到更低的最近支持档",
    "effort_clamp_up": "reasoning_effort 被夹到更高的最近支持档",
    "effort_dropped_unsupported": "provider family 不支持任何 reasoning effort，参数被丢弃",
    "strict_drop_effort": "strict 模式下模型未匹配任何已知 family，丢弃 reasoning 参数",
    "unknown_model": "模型未匹配任何已知 provider pattern，使用 openai 兜底",
}


class _WarningCapture(logging.Handler):
    """Capture provider warnings without changing the provider implementation."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextmanager
def _capture_provider_warnings() -> Iterator[_WarningCapture]:
    handler = _WarningCapture()
    providers.logger.addHandler(handler)
    try:
        yield handler
    finally:
        providers.logger.removeHandler(handler)


def classify_warning(message: str) -> str:
    """Map a provider warning message to its stable semantic category."""
    if "reasoning_effort alias" in message:
        return "deprecated_effort_alias"
    if "strict_unknown_models" in message and "dropping reasoning_effort" in message:
        return "strict_drop_effort"
    if "detect_provider: unknown model" in message:
        return "unknown_model"
    if "effort clamp" in message and "direction=upward" in message:
        return "effort_clamp_up"
    if "effort clamp" in message and "direction=downward" in message:
        return "effort_clamp_down"
    if "effort unsupported" in message and "actual=dropped" in message:
        return "effort_dropped_unsupported"
    if "cannot disable thinking" in message:
        return "disable_unsupported"
    raise ValueError(f"Unclassified provider warning: {message}")


def _warning_categories(records: list[logging.LogRecord]) -> list[str]:
    return [classify_warning(record.getMessage()) for record in records]


def _id_part(value: str | None) -> str:
    if value is None:
        return "none"
    if value == "":
        return "empty"
    if not value.strip():
        return "blank"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _vector_id(model: str, effort: str | None, strict: bool) -> str:
    strict_name = "strict" if strict else "lenient"
    return f"{_id_part(model)}__{_id_part(effort)}__{strict_name}"


def _build_vector(model: str, effort: str | None, strict: bool) -> dict[str, Any]:
    with _capture_provider_warnings() as captured:
        normalized_effort = providers.normalize_reasoning_effort(effort)
        detection = providers.detect_provider(model)
        translated = providers._translate(
            detection,
            normalized_effort,
            strict=strict,
            model=model,
        )

    return {
        "id": _vector_id(model, effort, strict),
        "input": {
            "model": model,
            "reasoning_effort": effort,
            "strict": strict,
        },
        "expect": {
            "family": detection.family,
            "matched": detection.matched,
            "set": translated,
            "warnings": _warning_categories(captured.records),
        },
    }


def build_conformance_document() -> dict[str, Any]:
    """Build the complete deterministic conformance document."""
    vectors = [
        _build_vector(model, effort, strict)
        for model in MODEL_CASES
        for effort in EFFORT_CASES
        for strict in (False, True)
    ]
    vectors.sort(key=lambda vector: vector["id"])

    return {
        "schema_version": 1,
        "generated_from": "src/llm_compat/providers.py",
        "generated_by": "scripts/export_conformance.py",
        "reviewed": False,
        "reviewed_note": "生成后待人工按覆盖轴审定；审定前不得作为跨语言契约使用。",
        "semantics": {
            "translation": (
                "只增不删：set 是要合并进请求体的字段，实现必须精确产出这些字段，不多不少。"
            ),
            "comparison": "exact",
        },
        "warning_categories": WARNING_CATEGORIES,
        "vectors": vectors,
    }


def export_conformance(output: Path = ROOT / "conformance.json") -> None:
    """Write the deterministic conformance document to ``output``."""
    document = build_conformance_document()
    output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "conformance.json",
        help="output path (default: repository root conformance.json)",
    )
    args = parser.parse_args()
    export_conformance(args.output)


if __name__ == "__main__":
    main()
