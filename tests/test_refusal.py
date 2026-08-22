from __future__ import annotations

import pytest

from llm_compat.refusal import (
    RefusalContext,
    RefusalEvidence,
    RefusalPolicy,
    check_response_keywords,
    check_structured_signals,
    detect_refusal,
)


def response(
    content: str | None = "hello",
    *,
    finish_reason: str = "stop",
    refusal: str | None = None,
) -> dict:
    message: dict[str, object] = {"content": content}
    if refusal is not None:
        message["refusal"] = refusal
    return {"choices": [{"finish_reason": finish_reason, "message": message}]}


class TestRefusalContext:
    def test_fields(self) -> None:
        ctx = RefusalContext(
            content="hello",
            status_code=200,
            model="deepseek-v4",
            provider="deepseek",
            finish_reason="stop",
        )
        assert ctx.content == "hello"
        assert ctx.model == "deepseek-v4"
        assert ctx.finish_reason == "stop"


class TestStructuredEvidence:
    @pytest.mark.parametrize(
        ("data", "layer"),
        [
            (response("answer", finish_reason="content_filter"), "structured_signal"),
            (response("answer", finish_reason="safety"), "structured_signal"),
            (response("answer", refusal="provider refusal"), "structured_signal"),
            (response(None), "malformed"),
            ({"choices": []}, "malformed"),
            ({}, "malformed"),
        ],
    )
    def test_structured_and_malformed_layers(self, data: dict, layer: str) -> None:
        evidence = detect_refusal(data)
        assert evidence.is_refusal is True
        assert evidence.layer == layer
        assert check_structured_signals(data) is True

    @pytest.mark.parametrize(
        "finish_reason",
        ["length", "stop"],
    )
    def test_non_refusal_finish_reasons(self, finish_reason: str) -> None:
        data = response("normal response", finish_reason=finish_reason)
        evidence = detect_refusal(data)
        assert evidence.is_refusal is False
        assert evidence.layer == "none"


class TestTextEvidence:
    @pytest.mark.parametrize(
        "content",
        [
            "我无法回答该问题",
            "我很抱歉，无法提供这项协助。",
            "抱歉，我作为一个 AI 不能继续。",
            "作为一个AI语言模型，我无法回答。",
            "这个问题不适合讨论。",
            "I cannot assist with that request.",
            "I'm sorry, but I am unable to help.",
            "As an AI language model, I cannot comply.",
            "This violates our content policy.",
            "That is against my programming.",
        ],
    )
    def test_strict_refusal_patterns(self, content: str) -> None:
        evidence = detect_refusal(response(content))
        assert evidence.is_refusal is True
        assert evidence.layer == "text_pattern"
        assert evidence.signal.startswith("pattern:")
        assert evidence.match_position < 120

    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            ("该内容涉及敏感话题", False),
            ("This violates", False),
            ("content policy", False),
            ("Python 是一种通用编程语言", False),
            ("Here is the code you requested", False),
            ("", False),
        ],
    )
    def test_old_topic_substrings_are_not_refusal_patterns(
        self, content: str, expected: bool
    ) -> None:
        assert check_response_keywords(content) is expected

    @pytest.mark.parametrize(
        ("content", "expected"),
        [
            ("我无法回答该问题", True),
            ("前置说明" * 40 + "我无法回答该问题", False),
            ("正常内容" + "x" * 5000 + "自定义拒绝", False),
        ],
    )
    def test_position_and_length_are_conjunctive_gates(
        self, content: str, expected: bool
    ) -> None:
        evidence = detect_refusal(
            response(content),
            policy=RefusalPolicy(extra_keywords=("自定义拒绝",)),
        )
        assert evidence.is_refusal is expected

    def test_issue_22_long_normal_response_is_not_refused(self) -> None:
        content = "合规分析正文。" * 2000
        content = content[:8000] + "违反" + content[8000:]
        content = content[:9000] + "无法提供" + content[9000:]
        content += "违反" * 3
        assert len(content) >= 13000
        evidence = detect_refusal(response(content))
        assert evidence.is_refusal is False
        assert evidence.layer == "none"

    def test_evidence_is_serializable_and_inferred(self) -> None:
        evidence = detect_refusal(response("我无法回答该问题"))
        assert isinstance(evidence, RefusalEvidence)
        assert evidence.is_inferred is True
        assert evidence.to_dict()["signal"] == "pattern:cn_first_person_cannot"


class TestDetectorStates:
    def test_true_is_a_custom_detector_refusal(self) -> None:
        def custom(ctx: RefusalContext) -> bool:
            return len(ctx.content) < 10

        evidence = detect_refusal(response("short"), custom_detector=custom)
        assert evidence.is_refusal is True
        assert evidence.layer == "custom_detector"

    def test_false_short_circuits_builtin_text_detection(self) -> None:
        def custom(ctx: RefusalContext) -> bool:
            return False

        evidence = detect_refusal(
            response("我无法回答该问题"), custom_detector=custom
        )
        assert evidence.is_refusal is False
        assert evidence.layer == "custom_override"

    @pytest.mark.parametrize(
        ("detector", "content", "expected"),
        [
            (None, "我无法回答该问题", True),
            (lambda ctx: None, "我无法回答该问题", True),
            (None, "正常内容", False),
            (lambda ctx: None, "正常内容", False),
        ],
    )
    def test_none_uses_builtin_text_detection(
        self, detector, content: str, expected: bool
    ) -> None:
        evidence = detect_refusal(
            response(content), custom_detector=detector
        )
        assert evidence.is_refusal is expected
        assert evidence.layer == ("text_pattern" if expected else "none")

    def test_detector_exception_continues_builtin_logic(self) -> None:
        def broken(ctx: RefusalContext) -> bool:
            raise ValueError("boom")

        evidence = detect_refusal(response("正常内容"), custom_detector=broken)
        assert evidence.is_refusal is False
        assert evidence.layer == "none"

    def test_detector_exception_does_not_suppress_builtin_refusal(self) -> None:
        def broken(ctx: RefusalContext) -> bool:
            raise ValueError("boom")

        evidence = detect_refusal(response("我无法回答该问题"), custom_detector=broken)
        assert evidence.is_refusal is True
        assert evidence.layer == "text_pattern"

    def test_structured_signal_cannot_be_overridden(self) -> None:
        evidence = detect_refusal(
            response("answer", finish_reason="content_filter"),
            custom_detector=lambda ctx: False,
        )
        assert evidence.is_refusal is True
        assert evidence.layer == "structured_signal"


class TestKeywordModes:
    @pytest.mark.parametrize(
        ("mode", "keywords", "expected"),
        [
            ("extend", ("manual",), True),
            ("replace", ("manual",), True),
            ("replace", (), False),
        ],
    )
    def test_keywords_mode(self, mode: str, keywords: tuple[str, ...], expected: bool) -> None:
        evidence = detect_refusal(
            response("manual refusal"),
            policy=RefusalPolicy(keywords_mode=mode, extra_keywords=keywords),  # type: ignore[arg-type]
        )
        assert evidence.is_refusal is expected

    def test_custom_keywords_are_substrings_with_same_gates(self) -> None:
        policy = RefusalPolicy(extra_keywords=("拒绝标记",))
        assert detect_refusal(response("这里是拒绝标记"), policy=policy).is_refusal is True
        long_content = "x" * 5000 + "拒绝标记"
        assert detect_refusal(response(long_content), policy=policy).is_refusal is False
