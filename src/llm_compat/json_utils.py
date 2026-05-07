from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

_CODE_FENCE_RE = re.compile(r"```(?:\w*)\s*\n(.*?)\n\s*```", re.DOTALL)


def parse_json(raw: str) -> Any:
    if not raw or not raw.strip():
        raise ValueError("Empty input")

    text = raw.strip()

    fence_match = _CODE_FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse JSON: {e}") from e


def _find_list_field(model_cls: type[BaseModel]) -> str | None:
    for name, field_info in model_cls.model_fields.items():
        annotation = field_info.annotation
        if (
            annotation is not None
            and hasattr(annotation, "__origin__")
            and annotation.__origin__ is list
        ):
            return name
    return None


def parse_json_model(raw: str, model_cls: type[T]) -> T:
    data = parse_json(raw)

    if isinstance(data, list):
        list_field = _find_list_field(model_cls)
        if list_field:
            data = {list_field: data}

    try:
        return model_cls.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"Pydantic validation failed: {e}") from e
