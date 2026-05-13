from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from ._collector import CollectorClient
from ._compat import normalize_reasoning_effort
from ._keyword_cache import get_cached_keywords
from ._types import ChatResult, LLMStats, TokenUsage
from .errors import ContentPolicyError, JSONParseError, SkipRequestError
from .fallback import filter_by_modality, resolve_fallback_chain
from .json_utils import parse_json, parse_json_model, pydantic_to_json_schema
from .providers import build_request_payload, describe_from_payload, detect_provider, get_provider_caps
from .refusal import RefusalDetector, detect_refusal
from .sensitive import SensitiveDetector

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def _is_format_error(error: Exception) -> bool:
    cause = error.__cause__ if error.__cause__ else error
    if isinstance(cause, httpx.HTTPStatusError):
        if cause.response.status_code == 400:
            body = cause.response.text.lower()
            return "response_format" in body or "json_schema" in body or "unsupported" in body
    if isinstance(error, httpx.HTTPStatusError):
        if error.response.status_code == 400:
            body = error.response.text.lower()
            return "response_format" in body or "json_schema" in body or "unsupported" in body
    return False


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
        max_concurrency: int | None = None,
        on_success: Callable[[str, int], None] | None = None,
        on_error: Callable[[str, Exception], None] | None = None,
        pre_request: Callable[[str], bool] | None = None,
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
        self._max_concurrency = max_concurrency
        self._semaphore: Any = None
        self._on_success = on_success
        self._on_error = on_error
        self._pre_request = pre_request
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

    def _simple_attempt(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        request_id: str,
        reasoning_effort: str | None = None,
        remaining_timeout: float | None = None,
        **extra: Any,
    ) -> Generator[_ChatRequest, _ChatResponse, ChatResult]:
        provider = detect_provider(model)
        payload = self._build_payload(model, messages, reasoning_effort, **extra)
        self._log_single_chat(payload, request_id, messages)

        resp = yield _ChatRequest(
            model=model, messages=messages, request_id=request_id,
            reasoning_effort=reasoning_effort, remaining_timeout=remaining_timeout,
            extra=extra,
        )
        if resp.error:
            raise resp.error

        assert resp.data is not None
        return self._extract_result(
            resp.data, model=model, provider=provider,
            latency_ms=resp.latency_ms, request_id=request_id,
        )

    def _json_attempt(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        request_id: str,
        schema: type[T] | None = None,
        json_schema: dict[str, Any] | None = None,
        self_correction: bool = False,
        max_retries: int = 2,
        reasoning_effort: str | None = None,
        remaining_timeout: float | None = None,
        **extra: Any,
    ) -> Generator[_ChatRequest, _ChatResponse, ChatResult]:
        provider = detect_provider(model)
        working_messages = list(messages)
        attempts = 0
        fallback_to_json_object = False

        while True:
            payload, effective_mode, final_messages = self._build_json_payload(
                model, working_messages,
                schema=schema, json_schema=json_schema,
                reasoning_effort=reasoning_effort,
                force_json_object=fallback_to_json_object, **extra,
            )

            self.stats.record_json_mode(effective_mode)
            self._log_single_chat(payload, request_id, final_messages)

            resp = yield _ChatRequest(
                model=model, messages=final_messages, request_id=request_id,
                reasoning_effort=reasoning_effort, remaining_timeout=remaining_timeout,
                extra={**extra, "response_format": payload.get("response_format")},
            )

            if resp.error:
                if (
                    effective_mode == "json_schema"
                    and not fallback_to_json_object
                    and _is_format_error(resp.error)
                ):
                    logger.warning(
                        "[%s] json_schema rejected by API, falling back to json_object",
                        request_id,
                    )
                    fallback_to_json_object = True
                    continue
                raise resp.error

            assert resp.data is not None
            result = self._extract_result(
                resp.data, model=model, provider=provider,
                latency_ms=resp.latency_ms, request_id=request_id,
            )

            try:
                if schema is not None:
                    parsed = parse_json_model(result.content, schema)
                else:
                    parsed = parse_json(result.content)
                result.parsed = parsed
                if attempts > 0:
                    self.stats.record_json_self_correction()
                return result
            except (ValueError, Exception) as parse_err:
                self.stats.record_json_parse_failure()
                attempts += 1

                if not self_correction or attempts > max_retries:
                    raise JSONParseError(
                        str(parse_err),
                        raw_content=result.content,
                        model=model,
                        request_id=request_id,
                    ) from parse_err

                working_messages = list(messages) + [
                    {"role": "assistant", "content": result.content},
                    {"role": "user", "content": f"Your response failed JSON parsing. Error: {parse_err}. Please return valid JSON matching the schema."},
                ]

    def _content_fallback_orchestrator(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        attempt_fn: Callable[..., Generator[_ChatRequest, _ChatResponse, ChatResult]],
        reasoning_effort: str | None = None,
        **extra: Any,
    ) -> Generator[_ChatRequest, _ChatResponse, ChatResult]:
        request_id = uuid.uuid4().hex[:8]
        chain = resolve_fallback_chain(model, self._content_fallbacks)
        deadline_start = time.monotonic()

        models_to_try = [model]
        prescan_skipped = False

        if self._sensitive_detector and chain and self._sensitive_detector.is_available:
            texts = [
                msg.get("content", "") if isinstance(msg.get("content"), str) else ""
                for msg in messages
            ]
            if self._sensitive_detector.contains_any(texts):
                self.stats.record_prescan_skip()
                needs_vision = self._has_vision_content(messages)
                effective_chain = filter_by_modality(chain, needs_vision=needs_vision)
                if effective_chain:
                    models_to_try = effective_chain
                    prescan_skipped = True
                    logger.info(
                        "[%s] LLM prescan | sensitive words detected | skipping %s → %s",
                        request_id, model, effective_chain[0],
                    )

        attempted: list[str] = []
        last_content = ""

        for current_model in models_to_try:
            elapsed = time.monotonic() - deadline_start
            remaining = self._total_timeout - elapsed
            if remaining <= 0 and attempted:
                break

            current_provider = detect_provider(current_model)

            if attempted:
                logger.info(
                    "[%s] LLM fallback | from=%s → to=%s | attempt=%d/%d",
                    request_id, model, current_model,
                    len(attempted), len(models_to_try),
                )

            inner = attempt_fn(
                current_model, messages,
                request_id=request_id,
                reasoning_effort=reasoning_effort,
                remaining_timeout=remaining if attempted else None,
                **extra,
            )

            try:
                request = next(inner)
                while True:
                    response: _ChatResponse = yield request

                    has_fallback = bool(chain) or len(models_to_try) > 1

                    if response.error and isinstance(response.error, ContentPolicyError):
                        if has_fallback:
                            inner.close()
                            http_status = None
                            cause = response.error.__cause__
                            if hasattr(cause, "response"):
                                http_status = getattr(cause.response, "status_code", None)
                            self._pending_refusal_report = {
                                "model": current_model,
                                "provider": current_provider,
                                "request_id": request_id,
                                "messages": messages,
                                "message_count": len(messages),
                                "has_images": self._has_vision_content(messages),
                                "detection_layer": "http_error",
                                "http_status": http_status,
                                "finish_reason": None,
                                "response_preview": str(response.error)[:200],
                            }
                            raise ContentPolicyError(str(response.error))

                    if has_fallback and response.data and not response.error:
                        if detect_refusal(
                            response.data,
                            self._refusal_detector,
                            extra_keywords=self._get_refusal_keywords(),
                            model=current_model,
                            provider=current_provider,
                        ):
                            inner.close()
                            choices = response.data.get("choices", [{}])
                            choice = choices[0] if choices else {}
                            last_content = choice.get("message", {}).get("content", "") or ""
                            self._pending_refusal_report = {
                                "model": current_model,
                                "provider": current_provider,
                                "request_id": request_id,
                                "messages": messages,
                                "message_count": len(messages),
                                "has_images": self._has_vision_content(messages),
                                "detection_layer": self._classify_refusal_layer(response.data),
                                "http_status": None,
                                "finish_reason": choice.get("finish_reason"),
                                "response_preview": last_content[:200],
                            }
                            raise ContentPolicyError(
                                f"Model {current_model} refused the request",
                            )

                    try:
                        request = inner.send(response)
                    except StopIteration as si:
                        result: ChatResult = si.value
                        if attempted or prescan_skipped:
                            result.fallback_from = model
                            result.fallback_chain = attempted
                        return result

            except ContentPolicyError:
                attempted.append(current_model)
                self.stats.record_fallback(refused_model=current_model)

                if not chain or (current_model == models_to_try[-1]):
                    needs_vision = self._has_vision_content(messages)
                    effective_chain = filter_by_modality(chain or [], needs_vision=needs_vision)
                    remaining_models = [m for m in effective_chain if m not in attempted and m not in models_to_try]
                    models_to_try.extend(remaining_models)

                    if current_model == models_to_try[-1]:
                        if not chain:
                            self.stats.record_error(model=current_model, error_type="ContentPolicyError")
                        raise ContentPolicyError(
                            f"All models refused: {attempted}",
                            attempted_models=attempted,
                            raw_content=last_content,
                            original_model=model,
                        )
                continue

            except Exception:
                self.stats.record_error(model=current_model, error_type=type(Exception).__name__)
                raise

        raise ContentPolicyError(
            f"All models refused: {attempted}",
            attempted_models=attempted,
            raw_content=last_content,
            original_model=model,
        )

    def _chat_orchestrator(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        reasoning_effort: str | None = None,
        **extra: Any,
    ) -> Generator[_ChatRequest, _ChatResponse, ChatResult]:
        return (yield from self._content_fallback_orchestrator(
            model, messages,
            attempt_fn=self._simple_attempt,
            reasoning_effort=reasoning_effort, **extra,
        ))

    def _invoke_pre_request(self, model: str) -> None:
        if self._pre_request is not None:
            result = self._pre_request(model)
            if result is False:
                raise SkipRequestError(f"pre_request hook returned False for model={model}")

    def _invoke_on_success(self, model: str, latency_ms: int) -> None:
        if self._on_success is not None:
            try:
                self._on_success(model, latency_ms)
            except Exception:
                logger.warning("on_success hook raised an exception", exc_info=True)

    def _invoke_on_error(self, model: str, error: Exception) -> None:
        if self._on_error is not None:
            try:
                self._on_error(model, error)
            except Exception:
                logger.warning("on_error hook raised an exception", exc_info=True)

    @staticmethod
    def _inject_schema_prompt(
        messages: list[dict[str, Any]],
        schema_dict: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Append JSON schema instruction to messages for json_object mode."""
        hint = (
            "\n\nRespond with valid JSON matching this schema:\n```json\n"
            + json.dumps(schema_dict, ensure_ascii=False)
            + "\n```"
        )
        msgs = [msg.copy() for msg in messages]
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i].get("role") != "user":
                continue
            content = msgs[i].get("content")
            if isinstance(content, str):
                msgs[i]["content"] = content + hint
                return msgs
            if isinstance(content, list):
                msgs[i]["content"] = list(content) + [{"type": "text", "text": hint}]
                return msgs
        msgs.append({"role": "user", "content": hint.lstrip()})
        return msgs

    def _build_json_payload(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        schema: type[BaseModel] | None = None,
        json_schema: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        force_json_object: bool = False,
        **extra: Any,
    ) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
        family = detect_provider(model)
        caps = get_provider_caps(family)
        json_mode = caps.get("json_mode", "json_object")

        use_json_schema = not force_json_object and json_mode == "json_schema"

        if json_schema is not None and use_json_schema:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "CustomSchema",
                    "strict": True,
                    "schema": json_schema,
                },
            }
            effective_mode = "json_schema"
        elif schema is not None and use_json_schema:
            response_format = {
                "type": "json_schema",
                "json_schema": pydantic_to_json_schema(schema),
            }
            effective_mode = "json_schema"
        else:
            response_format = {"type": "json_object"}
            effective_mode = "json_object"
            schema_dict = json_schema or (
                schema.model_json_schema() if schema is not None else None
            )
            if schema_dict is not None:
                messages = self._inject_schema_prompt(messages, schema_dict)

        payload = self._build_payload(
            model, messages, reasoning_effort,
            response_format=response_format, **extra,
        )
        return payload, effective_mode, messages

    def _json_chat_orchestrator(
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
    ) -> Generator[_ChatRequest, _ChatResponse, ChatResult]:
        from functools import partial
        attempt_fn = partial(
            self._json_attempt,
            schema=schema, json_schema=json_schema,
            self_correction=self_correction, max_retries=max_retries,
        )
        return (yield from self._content_fallback_orchestrator(
            model, messages,
            attempt_fn=attempt_fn,
            reasoning_effort=reasoning_effort, **extra,
        ))

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
