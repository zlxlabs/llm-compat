from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from ._compat import normalize_reasoning_effort
from ._types import ChatResult, LLMStats, TokenUsage
from .errors import ContentPolicyError, JSONParseError
from .fallback import filter_by_modality, resolve_fallback_chain
from .json_utils import parse_json, parse_json_model
from .providers import build_request_payload, describe_from_payload, detect_provider
from .refusal import RefusalDetector, detect_refusal
from .retry import async_retry_call
from .sensitive import SensitiveDetector

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)


class LLMClient:
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
        content_fallbacks: dict[str, list[str]] | None = None,
        refusal_detector: RefusalDetector | None = None,
        refusal_keywords: list[str] | None = None,
        sensitive_detector: SensitiveDetector | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout or _DEFAULT_TIMEOUT
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._total_timeout = total_timeout
        self._content_fallbacks = content_fallbacks
        self._refusal_detector = refusal_detector
        self._refusal_keywords = refusal_keywords
        self._sensitive_detector = sensitive_detector
        self._http: httpx.AsyncClient | None = None
        self.stats = LLMStats()

    def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
            )
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    async def __aenter__(self) -> LLMClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

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

    async def _request(self, payload: dict[str, Any], request_id: str) -> dict[str, Any]:
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

        desc = describe_from_payload(payload)
        logger.info(
            "[%s] LLM request | model=%s (%s) | thinking=%s | messages=%d",
            request_id, desc["model"], desc["provider"], desc["thinking_mode"], len(messages),
        )

        start = time.monotonic()
        timeout = remaining_timeout if remaining_timeout is not None else self._total_timeout

        async def _call() -> dict[str, Any]:
            return await self._request(payload, request_id)

        data = await async_retry_call(
            _call,
            max_retries=self._max_retries,
            base_delay=self._base_delay,
            max_delay=self._max_delay,
            total_timeout=timeout,
        )

        latency_ms = int((time.monotonic() - start) * 1000)
        return data, latency_ms

    def _extract_result(
        self,
        data: dict[str, Any],
        *,
        model: str,
        provider: str,
        latency_ms: int,
        request_id: str,
        fallback_from: str | None = None,
        fallback_chain: list[str] | None = None,
    ) -> ChatResult:
        content = data["choices"][0]["message"].get("content") or ""
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
            fallback_from=fallback_from,
            fallback_chain=fallback_chain or [],
        )

    async def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        reasoning_effort: str | None = None,
        **extra: Any,
    ) -> ChatResult:
        request_id = uuid.uuid4().hex[:8]
        provider = detect_provider(model)
        chain = resolve_fallback_chain(model, self._content_fallbacks)
        deadline_start = time.monotonic()

        # Pre-scan: skip primary if sensitive words detected and fallback available
        if self._sensitive_detector and chain and self._sensitive_detector.is_available:
            texts = [
                msg.get("content", "") if isinstance(msg.get("content"), str) else ""
                for msg in messages
            ]
            if self._sensitive_detector.contains_any(texts):
                self.stats.record_prescan_skip()
                logger.info(
                    "[%s] LLM prescan | sensitive words detected | skipping %s → %s",
                    request_id, model, chain[0],
                )
                needs_vision = self._has_vision_content(messages)
                effective_chain = filter_by_modality(chain, needs_vision=needs_vision)
                if effective_chain:
                    fb_model = effective_chain[0]
                    fb_provider = detect_provider(fb_model)
                    fb_data, fb_latency = await self._single_chat(
                        fb_model, messages, request_id,
                        reasoning_effort=reasoning_effort, **extra,
                    )
                    return self._extract_result(
                        fb_data, model=fb_model, provider=fb_provider,
                        latency_ms=fb_latency, request_id=request_id,
                        fallback_from=model, fallback_chain=[model],
                    )

        # Try primary model
        try:
            data, latency_ms = await self._single_chat(
                model, messages, request_id,
                reasoning_effort=reasoning_effort, **extra,
            )
        except ContentPolicyError:
            if not chain:
                self.stats.record_error(model=model, error_type="ContentPolicyError")
                raise
            data = None
            latency_ms = int((time.monotonic() - deadline_start) * 1000)
        except Exception:
            self.stats.record_error(model=model, error_type=type(Exception).__name__)
            raise

        if data is not None:
            if not detect_refusal(
                data,
                self._refusal_detector,
                extra_keywords=self._refusal_keywords,
                model=model,
                provider=provider,
            ):
                return self._extract_result(
                    data, model=model, provider=provider,
                    latency_ms=latency_ms, request_id=request_id,
                )

            if not chain:
                return self._extract_result(
                    data, model=model, provider=provider,
                    latency_ms=latency_ms, request_id=request_id,
                )

        # Primary refused — enter fallback loop
        self.stats.record_fallback(refused_model=model)
        logger.warning(
            "[%s] LLM fallback | model=%s refused | trying fallback chain",
            request_id, model,
        )

        needs_vision = self._has_vision_content(messages)
        effective_chain = filter_by_modality(chain, needs_vision=needs_vision)
        attempted = [model]
        last_content = (data["choices"][0]["message"].get("content") or "") if data else ""

        for fb_model in effective_chain:
            elapsed = time.monotonic() - deadline_start
            remaining = self._total_timeout - elapsed
            if remaining <= 0:
                break

            fb_provider = detect_provider(fb_model)
            logger.info(
                "[%s] LLM fallback | from=%s → to=%s | attempt=%d/%d",
                request_id, model, fb_model,
                len(attempted), len(effective_chain) + 1,
            )

            try:
                fb_data, fb_latency = await self._single_chat(
                    fb_model, messages, request_id,
                    reasoning_effort=reasoning_effort,
                    remaining_timeout=remaining,
                    **extra,
                )
            except ContentPolicyError:
                attempted.append(fb_model)
                self.stats.record_fallback(refused_model=fb_model)
                continue
            except Exception:
                self.stats.record_error(model=fb_model, error_type=type(Exception).__name__)
                raise

            if not detect_refusal(
                fb_data,
                self._refusal_detector,
                extra_keywords=self._refusal_keywords,
                model=fb_model,
                provider=fb_provider,
            ):
                total_latency = int((time.monotonic() - deadline_start) * 1000)
                return self._extract_result(
                    fb_data, model=fb_model, provider=fb_provider,
                    latency_ms=total_latency, request_id=request_id,
                    fallback_from=model, fallback_chain=attempted,
                )

            attempted.append(fb_model)
            self.stats.record_fallback(refused_model=fb_model)
            last_content = fb_data["choices"][0]["message"].get("content") or ""

        raise ContentPolicyError(
            f"All models refused: {attempted}",
            attempted_models=attempted,
            raw_content=last_content,
            original_model=model,
        )

    @staticmethod
    def _has_vision_content(messages: list[dict[str, Any]]) -> bool:
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        return True
        return False

    async def chat_json(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        schema: type[T] | None = None,
        reasoning_effort: str | None = None,
        **extra: Any,
    ) -> ChatResult:
        result = await self.chat(model, messages, reasoning_effort=reasoning_effort, **extra)
        raw = result.content

        try:
            if schema is not None:
                parsed = parse_json_model(raw, schema)
            else:
                parsed = parse_json(raw)
        except (ValueError, Exception) as e:
            raise JSONParseError(
                str(e),
                raw_content=raw,
                model=model,
                request_id=result.request_id,
            ) from e

        result.parsed = parsed
        return result

    async def chat_stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        reasoning_effort: str | None = None,
        **extra: Any,
    ) -> AsyncIterator[str]:
        request_id = uuid.uuid4().hex[:8]
        payload = self._build_payload(model, messages, reasoning_effort, stream=True, **extra)

        desc = describe_from_payload(payload)
        logger.info(
            "[%s] LLM stream | model=%s (%s) | thinking=%s",
            request_id, desc["model"], desc["provider"], desc["thinking_mode"],
        )

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
        return await self.chat(model, messages, reasoning_effort=reasoning_effort, **extra)

    async def chat_images(
        self,
        model: str,
        text: str,
        *,
        images: list[tuple[bytes, str]],
        reasoning_effort: str | None = None,
        **extra: Any,
    ) -> ChatResult:
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for img_data, img_type in images:
            b64 = base64.b64encode(img_data).decode()
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:{img_type};base64,{b64}"}}
            )
        messages = [{"role": "user", "content": content}]
        return await self.chat(model, messages, reasoning_effort=reasoning_effort, **extra)
