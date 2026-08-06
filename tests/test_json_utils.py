"""Tests for JSON response cleaning, Pydantic validation, and schema conversion."""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from llm_compat.json_utils import (
    parse_json,
    parse_json_model,
    parse_json_schema,
    pydantic_to_json_schema,
)


class TagResult(BaseModel):
    tags: list[str]


class SimpleResult(BaseModel):
    name: str
    score: float


class TestParseJson:
    def test_plain_json(self) -> None:
        assert parse_json('{"key": "value"}') == {"key": "value"}

    def test_code_fence_json(self) -> None:
        raw = '```json\n{"key": "value"}\n```'
        assert parse_json(raw) == {"key": "value"}

    def test_code_fence_no_lang(self) -> None:
        raw = '```\n{"key": "value"}\n```'
        assert parse_json(raw) == {"key": "value"}

    def test_whitespace_around(self) -> None:
        assert parse_json('  \n{"a": 1}\n  ') == {"a": 1}

    def test_bare_list(self) -> None:
        assert parse_json('["a", "b"]') == ["a", "b"]

    def test_code_fence_with_list(self) -> None:
        raw = '```json\n["a", "b"]\n```'
        assert parse_json(raw) == ["a", "b"]

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_json("not json at all")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_json("")

    def test_nested_code_fence(self) -> None:
        raw = 'Some text\n```json\n{"key": "value"}\n```\nMore text'
        assert parse_json(raw) == {"key": "value"}

    def test_multiple_code_fences_takes_first(self) -> None:
        raw = '```json\n{"a": 1}\n```\n```json\n{"b": 2}\n```'
        assert parse_json(raw) == {"a": 1}


class TestParseJsonSchema:
    schema = {
        "type": "object",
        "required": ["name", "score", "status", "profile"],
        "properties": {
            "name": {"type": "string"},
            "score": {"type": "number"},
            "status": {"type": "string", "enum": ["ready", "failed"]},
            "profile": {
                "type": "object",
                "required": ["city"],
                "properties": {"city": {"type": "string"}},
            },
        },
    }

    def test_valid_nested_object(self) -> None:
        raw = '{"name":"Ada","score":9.5,"status":"ready","profile":{"city":"Paris"}}'

        assert parse_json_schema(raw, self.schema) == {
            "name": "Ada",
            "score": 9.5,
            "status": "ready",
            "profile": {"city": "Paris"},
        }

    def test_missing_required_field_names_field(self) -> None:
        with pytest.raises(ValueError, match="name.*required.*missing"):
            parse_json_schema(
                '{"score":9.5,"status":"ready","profile":{"city":"Paris"}}',
                self.schema,
            )

    def test_wrong_type_names_field_expected_and_actual(self) -> None:
        with pytest.raises(ValueError, match="score.*number.*string"):
            parse_json_schema(
                '{"name":"Ada","score":"9.5","status":"ready",'
                '"profile":{"city":"Paris"}}',
                self.schema,
            )

    def test_missing_nested_required_field_names_nested_path(self) -> None:
        with pytest.raises(ValueError, match="profile.city.*required.*missing"):
            parse_json_schema(
                '{"name":"Ada","score":9.5,"status":"ready","profile":{}}',
                self.schema,
            )

    def test_invalid_enum_names_field_expected_and_actual(self) -> None:
        with pytest.raises(ValueError, match="status.*enum.*unknown"):
            parse_json_schema(
                '{"name":"Ada","score":9.5,"status":"unknown",'
                '"profile":{"city":"Paris"}}',
                self.schema,
            )

    def test_enum_number_distinguishes_boolean_and_accepts_json_numbers(self) -> None:
        schema = {"enum": [1, 2]}

        assert parse_json_schema("1", schema) == 1
        assert parse_json_schema("1.0", schema) == 1.0
        with pytest.raises(ValueError, match="expected enum.*True"):
            parse_json_schema("true", schema)

    def test_enum_boolean_distinguishes_number(self) -> None:
        schema = {"enum": [True]}

        assert parse_json_schema("true", schema) is True
        with pytest.raises(ValueError, match="expected enum.*1"):
            parse_json_schema("1", schema)

    def test_integer_accepts_integral_float_but_rejects_fraction_and_boolean(self) -> None:
        schema = {"type": "integer"}

        assert parse_json_schema("1", schema) == 1
        assert parse_json_schema("1.0", schema) == 1.0
        with pytest.raises(ValueError, match="expected integer.*number"):
            parse_json_schema("1.5", schema)
        with pytest.raises(ValueError, match="expected integer.*boolean"):
            parse_json_schema("true", schema)

    def test_array_items_validate_element_types_with_index_path(self) -> None:
        schema = {
            "type": "object",
            "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
        }

        assert parse_json_schema('{"tags":["a","b"]}', schema) == {"tags": ["a", "b"]}
        with pytest.raises(ValueError, match=r"\$\.tags\[1\].*string.*number"):
            parse_json_schema('{"tags":["a",1]}', schema)

    def test_array_items_validate_nested_required_fields_with_index_path(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "records": {
                    "type": "array",
                    "items": {"type": "object", "required": ["x"]},
                }
            },
        }

        with pytest.raises(ValueError, match=r"\$\.records\[0\]\.x.*required.*missing"):
            parse_json_schema('{"records":[{}]}', schema)

    def test_array_without_items_accepts_any_array_values(self) -> None:
        schema = {"type": "array"}
        raw = '["text", 1, true, {"key": "value"}]'

        assert parse_json_schema(raw, schema) == ["text", 1, True, {"key": "value"}]

    def test_unknown_keywords_are_ignored(self) -> None:
        schema = {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string", "minLength": 3}},
            "additionalProperties": False,
        }

        assert parse_json_schema('{"name":"A","extra":true}', schema) == {
            "name": "A",
            "extra": True,
        }


class TestParseJsonModel:
    def test_valid_object(self) -> None:
        result = parse_json_model('{"tags": ["python", "ai"]}', TagResult)
        assert isinstance(result, TagResult)
        assert result.tags == ["python", "ai"]

    def test_code_fence_wrapped(self) -> None:
        raw = '```json\n{"tags": ["test"]}\n```'
        result = parse_json_model(raw, TagResult)
        assert result.tags == ["test"]

    def test_bare_list_auto_wraps(self) -> None:
        raw = '["python", "ai"]'
        result = parse_json_model(raw, TagResult)
        assert result.tags == ["python", "ai"]

    def test_validation_error_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_json_model('{"name": 123}', TagResult)

    def test_non_list_model_no_wrap(self) -> None:
        result = parse_json_model('{"name": "test", "score": 9.5}', SimpleResult)
        assert result.name == "test"
        assert result.score == 9.5


class NestedModel(BaseModel):
    name: str
    items: list[str]
    score: float | None = None


class TestPydanticToJsonSchema:
    def test_simple_model(self) -> None:
        schema = pydantic_to_json_schema(TagResult)
        assert schema["name"] == "TagResult"
        assert schema["strict"] is True
        assert "schema" in schema
        props = schema["schema"]["properties"]
        assert "tags" in props

    def test_nested_model(self) -> None:
        schema = pydantic_to_json_schema(NestedModel)
        assert schema["name"] == "NestedModel"
        props = schema["schema"]["properties"]
        assert "name" in props
        assert "items" in props
        assert "score" in props

    def test_schema_is_valid_json_schema(self) -> None:
        schema = pydantic_to_json_schema(SimpleResult)
        inner = schema["schema"]
        assert inner["type"] == "object"
        assert "properties" in inner
        assert "required" in inner

    def test_custom_name(self) -> None:
        schema = pydantic_to_json_schema(TagResult, name="MyCustomName")
        assert schema["name"] == "MyCustomName"
