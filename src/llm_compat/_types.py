from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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

    def reset(self) -> None:
        self.total_calls = 0
        self.success_count = 0
        self.error_count = 0
        self.total_tokens = 0
        self.total_latency_ms = 0
        self._errors_by_type.clear()
