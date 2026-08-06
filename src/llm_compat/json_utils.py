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


def _clean_schema_for_strict(schema: dict[str, Any]) -> dict[str, Any]:
    schema.pop("title", None)
    if schema.get("type") == "object":
        schema["additionalProperties"] = False
    if "properties" in schema:
        for prop in schema["properties"].values():
            if isinstance(prop, dict):
                _clean_schema_for_strict(prop)
    if "items" in schema and isinstance(schema["items"], dict):
        _clean_schema_for_strict(schema["items"])
    if "$defs" in schema:
        for defn in schema["$defs"].values():
            if isinstance(defn, dict):
                _clean_schema_for_strict(defn)
    return schema


def pydantic_to_json_schema(
    model_cls: type[BaseModel],
    *,
    name: str | None = None,
) -> dict[str, Any]:
    schema = model_cls.model_json_schema()
    _clean_schema_for_strict(schema)
    return {
        "name": name or model_cls.__name__,
        "strict": True,
        "schema": schema,
    }


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
