from __future__ import annotations

import logging
import time
from typing import Any, TypeVar, cast

import httpx
from pydantic import BaseModel

from ._base import BaseClient, _CallSideEffects, _ChatRequest, _ChatResponse
from .retry import sync_retry_call

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class SyncLLMClient(BaseClient):
    _http: httpx.Client | None = None

    def _get_http(self) -> httpx.Client:
        if not hasattr(self, "_http") or self._http is None or self._http.is_closed:
            self._http = httpx.Client(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
            )
        return self._http

    def close(self) -> None:
        if hasattr(self, "_http") and self._http and not self._http.is_closed:
            self._http.close()

    def __enter__(self) -> SyncLLMClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        http = self._get_http()
        response = http.post("/chat/completions", json=payload)
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    def _single_chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        request_id: str,
        *,
        reasoning_effort: str | None = None,
        remaining_timeout: float | None = None,
        **extra: Any,
    ) -> tuple[dict[str, Any], int]:
        payload = self._build_payload(model, messages, reasoning_effort, **extra)
        self._log_single_chat(payload, request_id, messages)

        start = time.monotonic()
        timeout = remaining_timeout if remaining_timeout is not None else self._total_timeout

        def _call() -> dict[str, Any]:
            return self._request(payload)

        data = sync_retry_call(
            _call,
            max_retries=self._max_retries,
            base_delay=self._base_delay,
            max_delay=self._max_delay,
            total_timeout=timeout,
        )

        latency_ms = int((time.monotonic() - start) * 1000)
        return data, latency_ms

    def _drive_chat(
        self,
        gen: Any,
    ) -> Any:
        request: _ChatRequest = next(gen)
        while True:
            try:
                data, latency_ms = self._single_chat(
                    request.model, request.messages, request.request_id,
                    reasoning_effort=request.reasoning_effort,
                    remaining_timeout=request.remaining_timeout,
                    **request.extra,
                )
                response = _ChatResponse(data=data, latency_ms=latency_ms)
            except Exception as e:
                response = _ChatResponse(error=e)
            try:
                request = gen.send(response)
            except StopIteration as si:
                return si.value

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        reasoning_effort: str | None = None,
        **extra: Any,
    ) -> Any:
        self._invoke_pre_request(model)
        effects = _CallSideEffects()
        gen = self._chat_orchestrator(
            model, messages,
            reasoning_effort=reasoning_effort,
            _call_effects=effects,
            **extra,
        )
        try:
            result = self._drive_chat(gen)
        except Exception as e:
            self._invoke_on_error(model, e)
            raise
        self._invoke_on_success(model, result.latency_ms)
        return result

    def chat_json(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        schema: type[T] | None = None,
        json_schema: dict[str, Any] | None = None,
        self_correction: bool = False,
        max_retries: int = 2,
        reasoning_effort: str | None = None,
        **extra: Any,
    ) -> Any:
        self._invoke_pre_request(model)
        effects = _CallSideEffects()
        gen = self._json_chat_orchestrator(
            model, messages,
            schema=schema, json_schema=json_schema,
            self_correction=self_correction, max_retries=max_retries,
            reasoning_effort=reasoning_effort,
            _call_effects=effects,
            **extra,
        )
        try:
            result = self._drive_chat(gen)
        except Exception as e:
            self._invoke_on_error(model, e)
            raise
        self._invoke_on_success(model, result.latency_ms)
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
    ) -> Any:
        messages = self._build_image_messages(text, image_data, media_type)
        return self.chat(model, messages, reasoning_effort=reasoning_effort, **extra)
