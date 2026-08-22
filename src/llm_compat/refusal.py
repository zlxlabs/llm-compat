from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

RefusalLayer = Literal[
    "structured_signal",
    "malformed",
    "text_pattern",
    "custom_detector",
    "custom_override",
    "none",
]


@dataclass(frozen=True)
class RefusalEvidence:
    """一次拒绝判定的完整证据。"""

    is_refusal: bool
    layer: RefusalLayer
    signal: str = ""
    matched_text: str = ""
    match_position: int = -1
    content_length: int = 0
    finish_reason: str | None = None

    @property
    def is_inferred(self) -> bool:
        return self.layer in ("text_pattern", "custom_detector", "malformed")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RefusalPolicy:
    """文本层判定门槛。"""

    max_content_length: int = 300
    head_window: int = 120
    keywords_mode: Literal["extend", "replace"] = "extend"
    extra_keywords: tuple[str, ...] = ()


@dataclass(frozen=True)
class RefusalContext:
    content: str
    status_code: int | None
    model: str
    provider: str
    finish_reason: str | None


RefusalDetector = Callable[[RefusalContext], bool | None]

_CONTENT_FILTER_REASONS: frozenset[str] = frozenset({
    "content_filter",
    "content_policy",
    "safety",
})

DEFAULT_REFUSAL_PATTERNS_CN: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "pattern:cn_first_person_cannot",
        re.compile(r"我(?:很抱歉[，,、:\s]*)?(?:无法|不能)(?:提供|回答|协助|讨论|继续)"),
    ),
    (
        "pattern:cn_apology_meta",
        re.compile(r"(?:抱歉|对不起)[，,、:\s]*(?:我|作为)"),
    ),
    (
        "pattern:cn_as_ai_cannot",
        re.compile(
            r"作为(?:一个)?(?:AI|人工智能|语言模型)[^。！？!?\n]{0,80}(?:无法|不能)"
        ),
    ),
    (
        "pattern:cn_unsuitable_topic",
        re.compile(r"不适合(?:讨论|回答)"),
    ),
)

DEFAULT_REFUSAL_PATTERNS_EN: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "pattern:en_apology_cannot",
        re.compile(
            r"\bI(?:\s+am\s+sorry|['’]m\s+(?:sorry|afraid))"
            r"[^.!?\n]{0,80}\b(?:cannot|can['’]?t|unable\s+to)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "pattern:en_first_person_assistance",
        re.compile(
            r"\bI\s+can(?:not|['’]t)\s+(?:assist|help|provide|comply)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "pattern:en_as_ai_cannot",
        re.compile(
            r"\bAs\s+an?\s+(?:AI|artificial intelligence|language model)\b"
            r"[^.!?\n]{0,80}\b(?:cannot|can['’]?t|unable(?:\s+to)?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "pattern:en_policy_violation",
        re.compile(
            r"\bviolates?\s+(?:my|our|the)\s+"
            r"[^.!?\n]{0,80}\b(?:polic\w*|guideline\w*)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "pattern:en_against_guidelines",
        re.compile(r"\bagainst\s+my\s+(?:programming|guidelines)\b", re.IGNORECASE),
    ),
)

DEFAULT_REFUSAL_PATTERNS = DEFAULT_REFUSAL_PATTERNS_CN + DEFAULT_REFUSAL_PATTERNS_EN


def _content_and_finish_reason(
    data: dict[str, Any],
) -> tuple[list[Any] | None, dict[str, Any] | None, str, str | None]:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None, None, "", None
    choice = choices[0]
    if not isinstance(choice, dict):
        return choices, None, "", None
    message = choice.get("message")
    if not isinstance(message, dict):
        message = {}
    content = message.get("content")
    return choices, choice, content if isinstance(content, str) else "", choice.get(
        "finish_reason"
    )


def _structured_evidence(data: dict[str, Any]) -> RefusalEvidence | None:
    choices, choice, content, finish_reason = _content_and_finish_reason(data)
    if choices is None:
        return RefusalEvidence(
            is_refusal=True,
            layer="malformed",
            signal="choices_missing_or_empty",
            finish_reason=finish_reason,
        )
    if choice is None:
        return RefusalEvidence(
            is_refusal=True,
            layer="malformed",
            signal="choice_malformed",
            finish_reason=finish_reason,
        )

    message = choice.get("message")
    if not isinstance(message, dict):
        message = {}
    if finish_reason in _CONTENT_FILTER_REASONS:
        return RefusalEvidence(
            is_refusal=True,
            layer="structured_signal",
            signal=f"finish_reason={finish_reason}",
            content_length=len(content),
            finish_reason=finish_reason,
        )

    refusal = message.get("refusal")
    if isinstance(refusal, str) and refusal:
        return RefusalEvidence(
            is_refusal=True,
            layer="structured_signal",
            signal="message.refusal",
            matched_text=refusal[:80],
            match_position=-1,
            content_length=len(content),
            finish_reason=finish_reason,
        )

    if finish_reason == "stop" and message.get("content") is None:
        return RefusalEvidence(
            is_refusal=True,
            layer="malformed",
            signal="content_none",
            content_length=0,
            finish_reason=finish_reason,
        )
    return None


def check_structured_signals(data: dict[str, Any]) -> bool:
    """兼容旧调用方的结构化信号布尔检查。"""

    return _structured_evidence(data) is not None


def _text_evidence(
    content: str, finish_reason: str | None, policy: RefusalPolicy
) -> RefusalEvidence:
    content_length = len(content)
    base = {
        "content_length": content_length,
        "finish_reason": finish_reason,
    }
    if not content or content_length > policy.max_content_length:
        return RefusalEvidence(is_refusal=False, layer="none", **base)

    patterns: tuple[tuple[str, re.Pattern[str]], ...]
    if policy.keywords_mode == "extend":
        patterns = DEFAULT_REFUSAL_PATTERNS
    else:
        patterns = ()

    for signal, pattern in patterns:
        match = pattern.search(content)
        if match is not None and match.start() < policy.head_window:
            return RefusalEvidence(
                is_refusal=True,
                layer="text_pattern",
                signal=signal,
                matched_text=match.group(0)[:80],
                match_position=match.start(),
                **base,
            )

    for keyword in policy.extra_keywords:
        if not keyword:
            continue
        position = content.find(keyword)
        if position != -1 and position < policy.head_window:
            return RefusalEvidence(
                is_refusal=True,
                layer="text_pattern",
                signal=f"keyword:{keyword}",
                matched_text=keyword[:80],
                match_position=position,
                **base,
            )

    return RefusalEvidence(is_refusal=False, layer="none", **base)


def check_response_keywords(
    content: str,
    *,
    extra_keywords: list[str] | None = None,
    policy: RefusalPolicy | None = None,
) -> bool:
    """检查文本拒绝句式，保留旧函数名以兼容直接调用方。"""

    effective_policy = policy or RefusalPolicy(extra_keywords=tuple(extra_keywords or ()))
    return _text_evidence(content, None, effective_policy).is_refusal


def _log_refusal(evidence: RefusalEvidence, *, model: str) -> None:
    logger.warning(
        "Refusal detected | model=%s | layer=%s | signal=%s | matched_text=%r | "
        "position=%d | content_length=%d | finish_reason=%s",
        model,
        evidence.layer,
        evidence.signal,
        evidence.matched_text,
        evidence.match_position,
        evidence.content_length,
        evidence.finish_reason,
    )


def detect_refusal(
    data: dict[str, Any],
    custom_detector: RefusalDetector | None = None,
    *,
    policy: RefusalPolicy | None = None,
    model: str = "",
    provider: str = "",
) -> RefusalEvidence:
    """按声明层、调用方 detector、文本推断层顺序判定拒绝。"""

    structured = _structured_evidence(data)
    if structured is not None:
        _log_refusal(structured, model=model)
        return structured

    _, _, content, finish_reason = _content_and_finish_reason(data)
    effective_policy = policy or RefusalPolicy()
    if custom_detector is not None:
        context = RefusalContext(
            content=content,
            status_code=200,
            model=model,
            provider=provider,
            finish_reason=finish_reason,
        )
        try:
            custom_result = custom_detector(context)
        except Exception:
            logger.warning("Custom refusal detector raised an exception, ignoring", exc_info=True)
        else:
            if custom_result is True:
                evidence = RefusalEvidence(
                    is_refusal=True,
                    layer="custom_detector",
                    signal="custom_detector",
                    content_length=len(content),
                    finish_reason=finish_reason,
                )
                _log_refusal(evidence, model=model)
                return evidence
            if custom_result is False:
                return RefusalEvidence(
                    is_refusal=False,
                    layer="custom_override",
                    signal="custom_detector",
                    content_length=len(content),
                    finish_reason=finish_reason,
                )

    evidence = _text_evidence(content, finish_reason, effective_policy)
    if evidence.is_refusal:
        _log_refusal(evidence, model=model)
    return evidence
