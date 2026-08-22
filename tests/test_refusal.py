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


REFUSAL_PATTERN_CASES = [
    ("pattern:cn_first_person_cannot", "我无法提供确切数字", "直接拒绝本次提供请求"),
    ("pattern:cn_apology_meta", "抱歉，我不能协助这个请求", "道歉后由我明确拒绝"),
    (
        "pattern:cn_as_ai_cannot",
        "作为一个AI语言模型，我无法回答这个问题",
        "当前回答以 AI 身份明确拒绝",
    ),
    ("pattern:cn_unsuitable_topic", "这个问题不适合讨论", "当前问题被明确拒绝讨论"),
    (
        "pattern:en_apology_cannot",
        "I'm sorry, but I cannot help with that request.",
        "道歉后由我明确拒绝",
    ),
    (
        "pattern:en_first_person_assistance",
        "I cannot assist with that request.",
        "当前回答由我明确拒绝协助",
    ),
    (
        "pattern:en_as_ai_cannot",
        "As an AI language model, I cannot comply with this request.",
        "当前回答以 AI 身份明确拒绝",
    ),
    (
        "pattern:en_policy_violation",
        "This violates our content policy.",
        "当前请求被明确归为违反政策",
    ),
    (
        "pattern:en_against_guidelines",
        "That is against my programming.",
        "当前请求被明确归为违背约束",
    ),
]

NON_REFUSAL_PATTERN_CASES = [
    (
        "pattern:cn_first_person_cannot",
        "我无法提供确切数字，但可以给出区间：",
        "先拒绝精确数字、随后继续回答，不是拒绝本次任务",
    ),
    (
        "pattern:cn_apology_meta",
        "抱歉，我需要先了解一下你的具体需求",
        "道歉后的澄清请求没有拒绝 cue",
    ),
    (
        "pattern:cn_as_ai_cannot",
        "原文写道：作为一个AI语言模型，我无法回答这个问题，但给出了替代方案",
        "转述他人且继续提供方案，不是本次回答的拒绝",
    ),
    (
        "pattern:cn_unsuitable_topic",
        "原文认为该做法不适合讨论细节",
        "转述原文观点，且主语不是当前请求",
    ),
    (
        "pattern:en_apology_cannot",
        "I'm afraid I cannot give an exact number, but roughly 40%.",
        "先拒绝精确数字、随后继续回答，不是拒绝本次任务",
    ),
    (
        "pattern:en_first_person_assistance",
        "I cannot provide an exact number, but I can estimate 40%.",
        "限制一个细节后仍完成回答，不是拒绝本次任务",
    ),
    (
        "pattern:en_as_ai_cannot",
        "The article says: As an AI language model, I cannot answer that.",
        "转述文章内容，不是本次回答的拒绝",
    ),
    (
        "pattern:en_policy_violation",
        "The audit found the vendor violates the company's policy.",
        "审计转述第三方事实，不是拒绝当前请求",
    ),
    (
        "pattern:en_against_guidelines",
        "The documentation says that it is against my programming.",
        "转述文档内容，不是拒绝当前请求",
    ),
]


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

    @pytest.mark.parametrize(
        ("data", "signal"),
        [
            ({"choices": []}, "choices_missing_or_empty"),
            ({"choices": [None]}, "choice_malformed"),
        ],
    )
    def test_malformed_evidence_reports_content_length(
        self, data: dict, signal: str
    ) -> None:
        evidence = detect_refusal(data)
        assert evidence.layer == "malformed"
        assert evidence.signal == signal
        assert evidence.content_length == 0


class TestTextEvidence:
    @pytest.mark.parametrize(
        ("signal", "content", "review"),
        REFUSAL_PATTERN_CASES,
        ids=[case[0] for case in REFUSAL_PATTERN_CASES],
    )
    def test_every_default_pattern_has_a_refusal_case(
        self, signal: str, content: str, review: str
    ) -> None:
        assert review
        evidence = detect_refusal(response(content, finish_reason="stop"))
        assert evidence.is_refusal is True
        assert evidence.layer == "text_pattern"
        assert evidence.signal == signal
        assert evidence.match_position < 120
        assert len(content) <= 300

    @pytest.mark.parametrize(
        ("signal", "content", "review"),
        NON_REFUSAL_PATTERN_CASES,
        ids=[case[0] for case in NON_REFUSAL_PATTERN_CASES],
    )
    def test_every_default_pattern_rejects_its_false_positive(
        self, signal: str, content: str, review: str
    ) -> None:
        assert review
        evidence = detect_refusal(response(content, finish_reason="stop"))
        assert evidence.is_refusal is False, signal
        assert evidence.layer == "none"
        assert len(content) <= 300
        assert any(char not in "\n" for char in content[:120])

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
        # The long custom-keyword case has a late match, independently locking
        # the position gate rather than the length gate.
        evidence = detect_refusal(
            response(content),
            policy=RefusalPolicy(extra_keywords=("自定义拒绝",)),
        )
        assert evidence.is_refusal is expected

    def test_length_gate_rejects_long_builtin_match_at_head(self) -> None:
        content = "抱歉，我无法提供完整的逐字稿，但可以为你总结如下：" + "正文。" * 200
        assert len(content) > 300
        evidence = detect_refusal(response(content))
        assert evidence.is_refusal is False
        assert evidence.layer == "none"

    def test_length_gate_rejects_long_extra_keyword_at_head(self) -> None:
        content = "自定义拒绝" + "正文。" * 200
        assert len(content) > 300
        evidence = detect_refusal(
            response(content),
            policy=RefusalPolicy(extra_keywords=("自定义拒绝",)),
        )
        assert evidence.is_refusal is False
        assert evidence.layer == "none"

    def test_extra_keyword_position_gate_within_length_limit(self) -> None:
        keyword = "自定义拒绝"
        late_content = "正常内容" * 35 + keyword
        head_content = keyword + "正常内容" * 35
        assert len(late_content) == len(head_content) == 145
        assert len(late_content) <= 300
        assert late_content.find(keyword) == 140
        assert head_content.find(keyword) == 0

        late_evidence = detect_refusal(
            response(late_content), policy=RefusalPolicy(extra_keywords=(keyword,))
        )
        head_evidence = detect_refusal(
            response(head_content), policy=RefusalPolicy(extra_keywords=(keyword,))
        )
        assert late_evidence.is_refusal is False
        assert head_evidence.is_refusal is True

    @pytest.mark.parametrize(
        ("content_length", "expected"),
        [(300, True), (301, False)],
    )
    def test_length_gate_boundary_is_inclusive(
        self, content_length: int, expected: bool
    ) -> None:
        prefix = "我无法回答该问题"
        content = prefix + "x" * (content_length - len(prefix))
        evidence = detect_refusal(response(content))
        assert len(content) == content_length
        assert evidence.is_refusal is expected
        assert evidence.layer == ("text_pattern" if expected else "none")

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
        ("content", "mode", "keywords", "expected"),
        [
            ("manual refusal", "extend", ("manual",), True),
            ("manual refusal", "replace", ("manual",), True),
            ("manual refusal", "replace", (), False),
            ("I cannot assist with that request", "replace", (), False),
            ("I cannot assist with that request", "extend", (), True),
        ],
    )
    def test_keywords_mode(
        self, content: str, mode: str, keywords: tuple[str, ...], expected: bool
    ) -> None:
        evidence = detect_refusal(
            response(content),
            policy=RefusalPolicy(keywords_mode=mode, extra_keywords=keywords),  # type: ignore[arg-type]
        )
        assert evidence.is_refusal is expected

    def test_custom_keywords_are_substrings_with_same_gates(self) -> None:
        policy = RefusalPolicy(extra_keywords=("拒绝标记",))
        assert detect_refusal(response("这里是拒绝标记"), policy=policy).is_refusal is True
        long_content = "x" * 5000 + "拒绝标记"
        assert detect_refusal(response(long_content), policy=policy).is_refusal is False

    def test_compat_keyword_arguments_merge_with_policy(self) -> None:
        policy = RefusalPolicy(
            keywords_mode="replace",
            extra_keywords=("policy-keyword",),
        )
        assert check_response_keywords(
            "legacy-keyword", policy=policy, extra_keywords=["legacy-keyword"]
        ) is True
