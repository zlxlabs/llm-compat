from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

_CODE_FENCE_RE = re.compile(r"```(?:\w*)\s*\n(.*?)\n\s*```", re.DOTALL)

_JSON_SCHEMA_TYPES = frozenset(
    {"object", "array", "string", "number", "integer", "boolean", "null"}
)


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


def parse_json_schema(raw: str, schema: dict[str, Any]) -> Any:
    """Parse JSON and validate the supported subset of a JSON Schema dict.

    The validator intentionally covers only the constraints used by llm-compat
    callers. Unknown JSON Schema keywords are ignored so provider-specific
    extensions do not make otherwise valid responses fail locally.
    """
    data = parse_json(raw)
    _validate_json_schema_value(data, schema, "$")
    return data


def _validate_json_schema_value(
    value: Any,
    schema: dict[str, Any],
    path: str,
) -> None:
    schema_type = schema.get("type")
    if (
        isinstance(schema_type, str)
        and schema_type in _JSON_SCHEMA_TYPES
        and not _matches_json_schema_type(value, schema_type)
    ):
        raise ValueError(
            f"JSON schema validation failed at {path}: expected {schema_type}, "
            f"got {_json_value_type(value)}"
        )

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and not any(value == candidate for candidate in enum_values):
        raise ValueError(
            f"JSON schema validation failed at {path}: expected enum {enum_values!r}, "
            f"got {value!r}"
        )

    if not isinstance(value, dict):
        return

    required_fields = schema.get("required")
    if isinstance(required_fields, list):
        for field_name in required_fields:
            if isinstance(field_name, str) and field_name not in value:
                field_path = _json_schema_field_path(path, field_name)
                raise ValueError(
                    f"JSON schema validation failed at {field_path}: "
                    "expected required field, got missing"
                )

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return
    for field_name, field_schema in properties.items():
        if (
            isinstance(field_name, str)
            and field_name in value
            and isinstance(field_schema, dict)
        ):
            _validate_json_schema_value(
                value[field_name],
                field_schema,
                _json_schema_field_path(path, field_name),
            )


def _matches_json_schema_type(value: Any, schema_type: str) -> bool:
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "null":
        return value is None
    return True


def _json_value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


def _json_schema_field_path(parent: str, field_name: str) -> str:
    return f"{parent}.{field_name}"


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
