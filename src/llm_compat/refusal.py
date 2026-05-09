from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_REFUSAL_KEYWORDS_CN: tuple[str, ...] = (
    "我无法回答",
    "无法提供",
    "涉及敏感",
    "不适合讨论",
    "违反",
    "作为AI助手",
    "抱歉，我不能",
)

DEFAULT_REFUSAL_KEYWORDS_EN: tuple[str, ...] = (
    "I cannot assist",
    "I'm unable to",
    "content policy",
    "I can't help with",
    "violates",
    "against my guidelines",
)

_ALL_DEFAULT_KEYWORDS = DEFAULT_REFUSAL_KEYWORDS_CN + DEFAULT_REFUSAL_KEYWORDS_EN

_CONTENT_FILTER_REASONS: frozenset[str] = frozenset({
    "content_filter",
    "content_policy",
    "safety",
})


@dataclass
class RefusalContext:
    content: str
    status_code: int | None
    model: str
    provider: str
    finish_reason: str | None


RefusalDetector = Callable[[RefusalContext], bool]


def check_structured_signals(data: dict[str, Any]) -> bool:
    choices = data.get("choices")
    if not choices:
        return True

    choice = choices[0]
    finish_reason = choice.get("finish_reason", "")
    if finish_reason in _CONTENT_FILTER_REASONS:
        return True

    message = choice.get("message", {})
    if message.get("refusal"):
        return True

    if message.get("content") is None and finish_reason == "stop":
        return True

    return False


def check_response_keywords(
    content: str,
    *,
    extra_keywords: list[str] | None = None,
) -> bool:
    if not content:
        return False

    keywords = _ALL_DEFAULT_KEYWORDS
    if extra_keywords:
        keywords = keywords + tuple(extra_keywords)

    return any(kw in content for kw in keywords)


def detect_refusal(
    data: dict[str, Any],
    custom_detector: RefusalDetector | None = None,
    *,
    extra_keywords: list[str] | None = None,
    model: str = "",
    provider: str = "",
) -> bool:
    if check_structured_signals(data):
        return True

    choices = data.get("choices", [{}])
    content = ""
    finish_reason = None
    if choices:
        choice = choices[0]
        content = choice.get("message", {}).get("content", "") or ""
        finish_reason = choice.get("finish_reason")

    if custom_detector is not None:
        ctx = RefusalContext(
            content=content,
            status_code=200,
            model=model,
            provider=provider,
            finish_reason=finish_reason,
        )
        try:
            if custom_detector(ctx):
                return True
        except Exception:
            logger.warning("Custom refusal detector raised an exception, ignoring", exc_info=True)

    if check_response_keywords(content, extra_keywords=extra_keywords):
        return True

    return False
