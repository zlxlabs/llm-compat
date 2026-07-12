from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

_MAX_MODEL_ATTEMPTS = 100


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """A routing fact that did not necessarily result in an upstream request."""

    model: str
    action: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ModelAttempt:
    """One request sent to one model, excluding transport-level retries."""

    model: str
    provider: str
    json_mode: str
    trigger: str
    outcome: str
    error_kind: str | None = None
    http_status: int | None = None
    latency_ms: int = 0
    response_classification: str | None = None

    def to_dict(self) -> dict[str, str | int | None]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CallTrace:
    """Immutable, serialization-safe facts for one logical LLM call."""

    request_id: str
    requested_model: str
    started_at: str
    latency_ms: int
    route_decisions: tuple[RouteDecision, ...]
    model_attempts: tuple[ModelAttempt, ...]
    final_outcome: str
    final_model: str | None = None
    truncated: bool = False
    dropped_events: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "requested_model": self.requested_model,
            "started_at": self.started_at,
            "latency_ms": self.latency_ms,
            "route_decisions": [item.to_dict() for item in self.route_decisions],
            "model_attempts": [item.to_dict() for item in self.model_attempts],
            "final_outcome": self.final_outcome,
            "final_model": self.final_model,
            "truncated": self.truncated,
            "dropped_events": self.dropped_events,
        }


class _CallTraceBuilder:
    """Mutable per-call accumulator; only frozen snapshots cross the public API."""

    def __init__(self, *, request_id: str, requested_model: str) -> None:
        self.request_id = request_id
        self.requested_model = requested_model
        self._started_at = datetime.now(UTC).isoformat()
        self._started_monotonic = time.monotonic()
        self._route_decisions: list[RouteDecision] = []
        self._model_attempts: list[ModelAttempt] = []
        self._dropped_events = 0

    def add_route_decision(self, *, model: str, action: str, reason: str) -> None:
        self._route_decisions.append(RouteDecision(model=model, action=action, reason=reason))

    def add_model_attempt(
        self,
        *,
        model: str,
        provider: str,
        json_mode: str,
        trigger: str,
        outcome: str,
        error_kind: str | None = None,
        http_status: int | None = None,
        latency_ms: int = 0,
        response_classification: str | None = None,
    ) -> None:
        if len(self._model_attempts) >= _MAX_MODEL_ATTEMPTS:
            self._dropped_events += 1
            return
        self._model_attempts.append(
            ModelAttempt(
                model=model,
                provider=provider,
                json_mode=json_mode,
                trigger=trigger,
                outcome=outcome,
                error_kind=error_kind,
                http_status=http_status,
                latency_ms=latency_ms,
                response_classification=response_classification,
            )
        )

    def freeze(self, *, final_outcome: str, final_model: str | None = None) -> CallTrace:
        latency_ms = int((time.monotonic() - self._started_monotonic) * 1000)
        return CallTrace(
            request_id=self.request_id,
            requested_model=self.requested_model,
            started_at=self._started_at,
            latency_ms=latency_ms,
            route_decisions=tuple(self._route_decisions),
            model_attempts=tuple(self._model_attempts),
            final_outcome=final_outcome,
            final_model=final_model,
            truncated=self._dropped_events > 0,
            dropped_events=self._dropped_events,
        )
