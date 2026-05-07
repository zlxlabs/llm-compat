"""Tests for llm_compat.json_utils — JSON response cleaning and Pydantic validation."""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from llm_compat.json_utils import parse_json, parse_json_model


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
