from __future__ import annotations

import base64
import logging
import time
import uuid
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from ._collector import CollectorClient
from ._compat import normalize_reasoning_effort
from ._keyword_cache import get_cached_keywords
from ._types import ChatResult, LLMStats, TokenUsage
from .errors import ContentPolicyError, JSONParseError
from .fallback import filter_by_modality, resolve_fallback_chain
from .json_utils import parse_json, parse_json_model
from .providers import build_request_payload, describe_from_payload, detect_provider
from .refusal import RefusalDetector, detect_refusal
from .sensitive import SensitiveDetector

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)


@dataclass
class _ChatRequest:
    model: str
    messages: list[dict[str, Any]]
    request_id: str
    reasoning_effort: str | None
    remaining_timeout: float | None
    extra: dict[str, Any]


@dataclass
class _ChatResponse:
    data: dict[str, Any] | None = None
    latency_ms: int = 0
    error: Exception | None = None


class BaseClient:
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
        refusal_keywords_url: str | list[str] | None = None,
        sensitive_detector: SensitiveDetector | None = None,
        collector_url: str | None = None,
        collector_project: str = "",
        collector_api_key: str = "",
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
        self._refusal_keywords_manual = refusal_keywords
        self._refusal_keywords_urls: list[str] = []
        if refusal_keywords_url:
            if isinstance(refusal_keywords_url, str):
                self._refusal_keywords_urls = [refusal_keywords_url]
            else:
                self._refusal_keywords_urls = list(refusal_keywords_url)
            for url in self._refusal_keywords_urls:
                get_cached_keywords(url)
        self._sensitive_detector = sensitive_detector
        self._collector: CollectorClient | None = None
        if collector_url:
            self._collector = CollectorClient(
                url=collector_url, project=collector_project, api_key=collector_api_key,
            )
        self._pending_refusal_report: dict[str, Any] | None = None
        self.stats = LLMStats()

    def _get_refusal_keywords(self) -> list[str] | None:
        all_words: set[str] = set()
        if self._refusal_keywords_manual:
            all_words.update(self._refusal_keywords_manual)
        for url in self._refusal_keywords_urls:
            all_words.update(get_cached_keywords(url))
        no_urls = not self._refusal_keywords_urls
        if not all_words and no_urls and self._refusal_keywords_manual is None:
            return None
        return list(all_words)

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

    @staticmethod
    def _classify_refusal_layer(data: dict[str, Any]) -> str:
        from .refusal import check_response_keywords, check_structured_signals
        if check_structured_signals(data):
            return "structured_signal"
        choices = data.get("choices", [{}])
        content = choices[0].get("message", {}).get("content", "") if choices else ""
        if check_response_keywords(content):
            return "keyword_match"
        return "custom_detector"

    @staticmethod
    def _has_vision_content(messages: list[dict[str, Any]]) -> bool:
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        return True
        return False

    @staticmethod
    def _build_image_messages(
        text: str, image_data: bytes, media_type: str,
    ) -> list[dict[str, Any]]:
        b64 = base64.b64encode(image_data).decode()
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}},
                ],
            }
        ]

    @staticmethod
    def _build_multi_image_messages(
        text: str, images: list[tuple[bytes, str]],
    ) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        for img_data, img_type in images:
            b64 = base64.b64encode(img_data).decode()
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:{img_type};base64,{b64}"}}
            )
        return [{"role": "user", "content": content}]

    def _parse_json_result(
        self,
        result: ChatResult,
        model: str,
        schema: type[T] | None = None,
    ) -> ChatResult:
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

    def _chat_orchestrator(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        reasoning_effort: str | None = None,
        **extra: Any,
    ) -> Generator[_ChatRequest, _ChatResponse, ChatResult]:
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
                    resp = yield _ChatRequest(
                        model=fb_model, messages=messages, request_id=request_id,
                        reasoning_effort=reasoning_effort, remaining_timeout=None,
                        extra=extra,
                    )
                    if resp.error:
                        raise resp.error
                    assert resp.data is not None
                    return self._extract_result(
                        resp.data, model=fb_model, provider=fb_provider,
                        latency_ms=resp.latency_ms, request_id=request_id,
                        fallback_from=model, fallback_chain=[model],
                    )

        # Try primary model
        resp = yield _ChatRequest(
            model=model, messages=messages, request_id=request_id,
            reasoning_effort=reasoning_effort, remaining_timeout=None,
            extra=extra,
        )

        data: dict[str, Any] | None
        latency_ms: int

        refusal_detail: dict[str, Any] = {}

        if resp.error:
            if isinstance(resp.error, ContentPolicyError) and chain:
                data = None
                latency_ms = int((time.monotonic() - deadline_start) * 1000)
                http_status = None
                cause = resp.error.__cause__
                if hasattr(cause, "response"):
                    http_status = getattr(cause.response, "status_code", None)
                refusal_detail = {
                    "detection_layer": "http_error",
                    "http_status": http_status,
                    "finish_reason": None,
                    "response_preview": str(resp.error)[:200],
                }
            elif isinstance(resp.error, ContentPolicyError):
                self.stats.record_error(model=model, error_type="ContentPolicyError")
                raise resp.error
            else:
                self.stats.record_error(model=model, error_type=type(resp.error).__name__)
                raise resp.error
        else:
            data = resp.data
            latency_ms = resp.latency_ms

        if data is not None:
            if not detect_refusal(
                data,
                self._refusal_detector,
                extra_keywords=self._get_refusal_keywords(),
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

            choices = data.get("choices", [{}])
            choice = choices[0] if choices else {}
            content = choice.get("message", {}).get("content", "") or ""
            refusal_detail = {
                "detection_layer": self._classify_refusal_layer(data),
                "http_status": None,
                "finish_reason": choice.get("finish_reason"),
                "response_preview": content[:200],
            }

        # Primary refused — enter fallback loop
        self.stats.record_fallback(refused_model=model)
        self._pending_refusal_report = {
            "model": model,
            "provider": provider,
            "request_id": request_id,
            "messages": messages,
            "message_count": len(messages),
            "has_images": self._has_vision_content(messages),
            **refusal_detail,
        }
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

            resp = yield _ChatRequest(
                model=fb_model, messages=messages, request_id=request_id,
                reasoning_effort=reasoning_effort, remaining_timeout=remaining,
                extra=extra,
            )

            if resp.error:
                if isinstance(resp.error, ContentPolicyError):
                    attempted.append(fb_model)
                    self.stats.record_fallback(refused_model=fb_model)
                    continue
                self.stats.record_error(model=fb_model, error_type=type(resp.error).__name__)
                raise resp.error

            assert resp.data is not None
            if not detect_refusal(
                resp.data,
                self._refusal_detector,
                extra_keywords=self._get_refusal_keywords(),
                model=fb_model,
                provider=fb_provider,
            ):
                total_latency = int((time.monotonic() - deadline_start) * 1000)
                return self._extract_result(
                    resp.data, model=fb_model, provider=fb_provider,
                    latency_ms=total_latency, request_id=request_id,
                    fallback_from=model, fallback_chain=attempted,
                )

            attempted.append(fb_model)
            self.stats.record_fallback(refused_model=fb_model)
            last_content = resp.data["choices"][0]["message"].get("content") or ""

        raise ContentPolicyError(
            f"All models refused: {attempted}",
            attempted_models=attempted,
            raw_content=last_content,
            original_model=model,
        )

    def _log_single_chat(
        self,
        payload: dict[str, Any],
        request_id: str,
        messages: list[dict[str, Any]],
    ) -> None:
        desc = describe_from_payload(payload)
        logger.info(
            "[%s] LLM request | model=%s (%s) | thinking=%s | messages=%d",
            request_id, desc["model"], desc["provider"], desc["thinking_mode"], len(messages),
        )
