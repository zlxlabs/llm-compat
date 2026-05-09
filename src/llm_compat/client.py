from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from ._base import BaseClient, _ChatRequest, _ChatResponse
from .retry import async_retry_call

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMClient(BaseClient):
    def _get_http(self) -> httpx.AsyncClient:
        if not hasattr(self, "_http") or self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
            )
        return self._http

    async def close(self) -> None:
        if hasattr(self, "_http") and self._http and not self._http.is_closed:
            await self._http.aclose()

    async def __aenter__(self) -> LLMClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        http = self._get_http()
        response = await http.post("/chat/completions", json=payload)
        response.raise_for_status()
        return response.json()

    async def _single_chat(
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

        async def _call() -> dict[str, Any]:
            return await self._request(payload)

        data = await async_retry_call(
            _call,
            max_retries=self._max_retries,
            base_delay=self._base_delay,
            max_delay=self._max_delay,
            total_timeout=timeout,
        )

        latency_ms = int((time.monotonic() - start) * 1000)
        return data, latency_ms

    async def _drive_chat(
        self,
        gen: Any,
    ) -> Any:
        request: _ChatRequest = next(gen)
        while True:
            try:
                data, latency_ms = await self._single_chat(
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

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        reasoning_effort: str | None = None,
        **extra: Any,
    ) -> Any:
        gen = self._chat_orchestrator(model, messages, reasoning_effort=reasoning_effort, **extra)
        result = await self._drive_chat(gen)
        await self._maybe_report_refusal()
        return result

    async def _maybe_report_refusal(self) -> None:
        report = self._pending_refusal_report
        self._pending_refusal_report = None
        if report and self._collector:
            try:
                preview = self._collector.extract_preview(report["messages"])
                await self._collector.report_refusal(
                    model=report["model"],
                    error_type=report["error_type"],
                    input_text=preview,
                    provider=report.get("provider", ""),
                )
            except Exception:
                pass

    async def chat_json(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        schema: type[T] | None = None,
        reasoning_effort: str | None = None,
        **extra: Any,
    ) -> Any:
        result = await self.chat(model, messages, reasoning_effort=reasoning_effort, **extra)
        return self._parse_json_result(result, model, schema)

    async def chat_stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        reasoning_effort: str | None = None,
        **extra: Any,
    ) -> AsyncIterator[str]:
        import uuid

        request_id = uuid.uuid4().hex[:8]
        payload = self._build_payload(model, messages, reasoning_effort, stream=True, **extra)
        self._log_single_chat(payload, request_id, messages)

        http = self._get_http()
        async with http.stream("POST", "/chat/completions", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
                except (json.JSONDecodeError, IndexError, KeyError):
                    continue

    async def chat_image(
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
        return await self.chat(model, messages, reasoning_effort=reasoning_effort, **extra)

    async def chat_images(
        self,
        model: str,
        text: str,
        *,
        images: list[tuple[bytes, str]],
        reasoning_effort: str | None = None,
        **extra: Any,
    ) -> Any:
        messages = self._build_multi_image_messages(text, images)
        return await self.chat(model, messages, reasoning_effort=reasoning_effort, **extra)
