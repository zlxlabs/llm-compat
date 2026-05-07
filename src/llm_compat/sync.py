from __future__ import annotations

import base64
import logging
import time
import uuid
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from ._compat import normalize_reasoning_effort
from ._types import ChatResult, LLMStats, TokenUsage
from .errors import JSONParseError
from .json_utils import parse_json, parse_json_model
from .providers import build_request_payload, describe_from_payload, detect_provider
from .retry import sync_retry_call

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)


class SyncLLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: httpx.Timeout | None = None,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        total_timeout: float = 300.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout or _DEFAULT_TIMEOUT
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._total_timeout = total_timeout
        self._http: httpx.Client | None = None
        self.stats = LLMStats()

    def _get_http(self) -> httpx.Client:
        if self._http is None or self._http.is_closed:
            self._http = httpx.Client(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
            )
        return self._http

    def close(self) -> None:
        if self._http and not self._http.is_closed:
            self._http.close()

    def __enter__(self) -> SyncLLMClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _build_payload(
        self,
        model: str,
        messages: list[dict[str, Any]],
        reasoning_effort: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        effort = normalize_reasoning_effort(reasoning_effort)
        base: dict[str, Any] = {"model": model, "messages": messages, **extra}
        return build_request_payload(model, effort, base)

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        http = self._get_http()
        response = http.post("/chat/completions", json=payload)
        response.raise_for_status()
        return response.json()

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        reasoning_effort: str | None = None,
        **extra: Any,
    ) -> ChatResult:
        request_id = uuid.uuid4().hex[:8]
        payload = self._build_payload(model, messages, reasoning_effort, **extra)
        provider = detect_provider(model)

        desc = describe_from_payload(payload)
        logger.info(
            "[%s] LLM request | model=%s (%s) | thinking=%s | messages=%d",
            request_id, desc["model"], desc["provider"], desc["thinking_mode"], len(messages),
        )

        start = time.monotonic()

        def _call() -> dict[str, Any]:
            return self._request(payload)

        try:
            data = sync_retry_call(
                _call,
                max_retries=self._max_retries,
                base_delay=self._base_delay,
                max_delay=self._max_delay,
                total_timeout=self._total_timeout,
            )
        except Exception:
            self.stats.record_error(model=model, error_type=type(Exception).__name__)
            raise

        latency_ms = int((time.monotonic() - start) * 1000)
        content = data["choices"][0]["message"]["content"]
        usage_data = data.get("usage", {})
        usage = TokenUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        )

        logger.info(
            "[%s] LLM response | latency=%dms | tokens=%d/%d/%d",
            request_id, latency_ms,
            usage.prompt_tokens, usage.completion_tokens, usage.total_tokens,
        )

        self.stats.record_success(model=model, latency_ms=latency_ms, tokens=usage.total_tokens)

        return ChatResult(
            content=content,
            usage=usage,
            latency_ms=latency_ms,
            request_id=request_id,
            model=model,
            provider=provider,
        )

    def chat_json(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        schema: type[T] | None = None,
        reasoning_effort: str | None = None,
        **extra: Any,
    ) -> ChatResult:
        result = self.chat(model, messages, reasoning_effort=reasoning_effort, **extra)
        raw = result.content

        try:
            if schema is not None:
                parsed = parse_json_model(raw, schema)
            else:
                parsed = parse_json(raw)
        except (ValueError, Exception) as e:
            raise JSONParseError(
                str(e), raw_content=raw, model=model, request_id=result.request_id,
            ) from e

        result.parsed = parsed
        return result

    def chat_image(
        self,
        model: str,
        text: str,
        *,
        image_data: bytes,
        media_type: str,
        reasoning_effort: str | None = None,
        **extra: Any,
    ) -> ChatResult:
        b64 = base64.b64encode(image_data).decode()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}},
                ],
            }
        ]
        return self.chat(model, messages, reasoning_effort=reasoning_effort, **extra)
