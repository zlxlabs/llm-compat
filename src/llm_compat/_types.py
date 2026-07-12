from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._trace import CallTrace


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ChatResult:
    content: str
    parsed: Any | None = None
    usage: TokenUsage | None = None
    latency_ms: int = 0
    request_id: str = ""
    model: str = ""
    provider: str = ""
    fallback_from: str | None = None
    fallback_chain: list[str] = field(default_factory=list)
    trace: CallTrace | None = None

    def __str__(self) -> str:
        return self.content


@dataclass
class ProviderCaps:
    efforts: frozenset[str]
    disable_mode: str
    max_effort: str


@dataclass
class LLMStats:
    total_calls: int = 0
    success_count: int = 0
    error_count: int = 0
    total_tokens: int = 0
    total_latency_ms: int = 0
    fallback_count: int = 0
    prescan_skips: int = 0
    json_schema_calls: int = 0
    json_object_calls: int = 0
    json_parse_failures: int = 0
    json_self_correction_success: int = 0
    _refusal_counts: dict[str, int] = field(default_factory=dict)
    _errors_by_type: dict[str, int] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return self.success_count / self.total_calls

    def record_success(self, *, model: str, latency_ms: int, tokens: int) -> None:
        self.total_calls += 1
        self.success_count += 1
        self.total_latency_ms += latency_ms
        self.total_tokens += tokens

    def record_error(self, *, model: str, error_type: str) -> None:
        self.total_calls += 1
        self.error_count += 1
        self._errors_by_type[error_type] = self._errors_by_type.get(error_type, 0) + 1

    def record_fallback(self, *, refused_model: str) -> None:
        self.fallback_count += 1
        self._refusal_counts[refused_model] = self._refusal_counts.get(refused_model, 0) + 1

    def record_prescan_skip(self) -> None:
        self.prescan_skips += 1

    def record_json_mode(self, mode: str) -> None:
        if mode == "json_schema":
            self.json_schema_calls += 1
        elif mode == "json_object":
            self.json_object_calls += 1

    def record_json_parse_failure(self) -> None:
        self.json_parse_failures += 1

    def record_json_self_correction(self) -> None:
        self.json_self_correction_success += 1

    def reset(self) -> None:
        self.total_calls = 0
        self.success_count = 0
        self.error_count = 0
        self.total_tokens = 0
        self.total_latency_ms = 0
        self.fallback_count = 0
        self.prescan_skips = 0
        self.json_schema_calls = 0
        self.json_object_calls = 0
        self.json_parse_failures = 0
        self.json_self_correction_success = 0
        self._refusal_counts.clear()
        self._errors_by_type.clear()
