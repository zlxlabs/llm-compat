from __future__ import annotations

import json
import logging

from pydantic import BaseModel
from pytest_httpx import HTTPXMock

from llm_compat._base import BaseClient
from llm_compat._compat import validate_config, validate_fallback_config
from llm_compat.client import LLMClient
from llm_compat.fallback import filter_by_modality
from llm_compat.providers import build_request_payload, detect_provider
from llm_compat.sensitive import SensitiveDetector
from llm_compat.sync import SyncLLMClient


class _Tags(BaseModel):
    tags: list[str]


def _chat_response(content: str = '{"tags": ["ok"]}') -> dict:
    return {
        "id": "chatcmpl-strict-unknown",
        "object": "chat.completion",
        "model": "qwen-max",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class TestStrictUnknownProviderPayload:
    def test_detection_reports_unknown_model(self) -> None:
        detection = detect_provider("qwen-max")

        assert detection.family == "openai"
        assert detection.matched is False

    def test_unknown_reasoning_is_kept_by_default(self) -> None:
        payload = build_request_payload(
            "qwen-max",
            "high",
            {"model": "qwen-max", "messages": []},
        )

        assert payload["reasoning_effort"] == "high"

    def test_unknown_reasoning_is_dropped_in_strict_mode(
        self,
        caplog,
    ) -> None:
        with caplog.at_level(logging.WARNING):
            payload = build_request_payload(
                "qwen-max",
                "high",
                {"model": "qwen-max", "messages": []},
                strict=True,
            )

        assert "reasoning_effort" not in payload
        assert "thinking" not in payload
        assert any(
            "strict_unknown_models" in record.getMessage()
            and "dropping reasoning_effort" in record.getMessage()
            for record in caplog.records
        )

    def test_unknown_disabled_reasoning_is_also_dropped_in_strict_mode(self) -> None:
        payload = build_request_payload(
            "qwen-max",
            "disabled",
            {"model": "qwen-max", "messages": []},
            strict=True,
        )

        assert "reasoning_effort" not in payload
        assert "thinking" not in payload

    def test_known_model_is_not_changed_by_strict_mode(self) -> None:
        base = {"model": "deepseek-chat", "messages": []}

        assert build_request_payload("deepseek-chat", "high", base) == build_request_payload(
            "deepseek-chat", "high", base, strict=True,
        )
        assert build_request_payload(
            "deepseek-chat", "disabled", base,
        ) == build_request_payload("deepseek-chat", "disabled", base, strict=True)

    def test_unknown_json_mode_downgrades_to_json_object(self, caplog) -> None:
        client = BaseClient(
            "http://test",
            "test-key",
            strict_unknown_models=True,
        )

        with caplog.at_level(logging.WARNING):
            payload, effective_mode, _ = client._build_json_payload(
                "qwen-max",
                [],
                schema=_Tags,
                reasoning_effort="high",
            )

        assert effective_mode == "json_object"
        assert payload["response_format"] == {"type": "json_object"}
        assert "reasoning_effort" not in payload
        assert any(
            "strict_unknown_models" in record.getMessage()
            and "downgrading json_mode" in record.getMessage()
            for record in caplog.records
        )

    def test_known_json_mode_is_not_changed_by_strict_mode(self) -> None:
        client = BaseClient("http://test", "test-key", strict_unknown_models=True)

        _, effective_mode, _ = client._build_json_payload("gpt-5-mini", [], schema=_Tags)

        assert effective_mode == "json_schema"


class TestStrictUnknownFallbacks:
    def test_unknown_models_are_removed_from_vision_chain_only_in_strict_mode(
        self,
        caplog,
    ) -> None:
        chain = ["qwen-max", "gpt-4.1-mini", "deepseek-chat"]

        with caplog.at_level(logging.WARNING):
            filtered = filter_by_modality(chain, needs_vision=True, strict=True)

        assert filtered == ["gpt-4.1-mini"]
        assert any(
            "strict_unknown_models" in record.getMessage()
            and "qwen-max" in record.getMessage()
            and "vision fallback chain" in record.getMessage()
            for record in caplog.records
        )
        assert filter_by_modality(chain, needs_vision=True) == [
            "qwen-max", "gpt-4.1-mini",
        ]

    def test_strict_filter_keeps_unknown_models_for_text_requests(self) -> None:
        chain = ["qwen-max", "gpt-4.1-mini"]

        assert filter_by_modality(chain, needs_vision=False, strict=True) == chain

    def test_validate_config_predicts_strict_unknown_drop(self) -> None:
        assert validate_config("qwen-max", "high") == []

        warnings = validate_config("qwen-max", "high", strict=True)

        assert len(warnings) == 1
        assert "strict_unknown_models" in warnings[0]
        assert "drop reasoning_effort" in warnings[0]

    def test_validate_fallback_config_does_not_count_unknown_in_strict_mode(self) -> None:
        config = {"gpt-4.1-*": ["qwen-max"]}

        assert validate_fallback_config(config) == []
        warnings = validate_fallback_config(config, strict=True)

        assert len(warnings) == 1
        assert "vision" in warnings[0].lower()


class TestStrictUnknownClients:
    def test_client_constructor_defaults_to_lenient(self) -> None:
        client = LLMClient(base_url="http://test", api_key="test-key")
        sync_client = SyncLLMClient(base_url="http://test", api_key="test-key")

        assert client._strict_unknown_models is False
        assert sync_client._strict_unknown_models is False

    async def test_async_client_drops_reasoning_and_downgrades_json(
        self,
        httpx_mock: HTTPXMock,
    ) -> None:
        httpx_mock.add_response(json=_chat_response())
        async with LLMClient(
            base_url="http://test/v1",
            api_key="sk-test",
            strict_unknown_models=True,
        ) as client:
            result = await client.chat_json(
                "qwen-max",
                [{"role": "user", "content": "json"}],
                schema=_Tags,
                reasoning_effort="disabled",
            )

        assert result.parsed.tags == ["ok"]
        body = json.loads(httpx_mock.get_request().content)
        assert body["response_format"] == {"type": "json_object"}
        assert "reasoning_effort" not in body
        assert "thinking" not in body

    async def test_all_unknown_vision_fallbacks_use_existing_empty_chain_trace(
        self,
        httpx_mock: HTTPXMock,
    ) -> None:
        httpx_mock.add_response(json=_chat_response("primary"))
        async with LLMClient(
            base_url="http://test/v1",
            api_key="sk-test",
            content_fallbacks={"qwen-max": ["qwen-fallback"]},
            sensitive_detector=SensitiveDetector(words=["sensitive"]),
            strict_unknown_models=True,
        ) as client:
            result = await client.chat(
                "qwen-max",
                [
                    {"role": "user", "content": "sensitive request"},
                    {
                        "role": "user",
                        "content": [{
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,abc"},
                        }],
                    },
                ],
            )

        assert result.model == "qwen-max"
        assert len(httpx_mock.get_requests()) == 1
        assert any(
            decision.reason == "no_eligible_fallback"
            for decision in result.trace.route_decisions
        )
