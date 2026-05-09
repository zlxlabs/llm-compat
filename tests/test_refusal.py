from __future__ import annotations

import pytest

from llm_compat.refusal import (
    DEFAULT_REFUSAL_KEYWORDS_CN,
    DEFAULT_REFUSAL_KEYWORDS_EN,
    RefusalContext,
    check_response_keywords,
    check_structured_signals,
    detect_refusal,
)


class TestRefusalContext:
    def test_fields(self):
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


class TestCheckStructuredSignals:
    def test_finish_reason_content_filter(self):
        data = {"choices": [{"finish_reason": "content_filter", "message": {"content": "..."}}]}
        assert check_structured_signals(data) is True

    def test_empty_choices(self):
        data = {"choices": []}
        assert check_structured_signals(data) is True

    def test_no_choices_key(self):
        data = {}
        assert check_structured_signals(data) is True

    def test_normal_response(self):
        data = {"choices": [{"finish_reason": "stop", "message": {"content": "hello"}}]}
        assert check_structured_signals(data) is False

    def test_finish_reason_length(self):
        data = {"choices": [{"finish_reason": "length", "message": {"content": "truncated"}}]}
        assert check_structured_signals(data) is False

    def test_refusal_field(self):
        data = {"choices": [{"message": {"refusal": "I can't help with that"}, "finish_reason": "stop"}]}
        assert check_structured_signals(data) is True

    def test_none_content_with_stop(self):
        data = {"choices": [{"finish_reason": "stop", "message": {"content": None}}]}
        assert check_structured_signals(data) is True


class TestCheckResponseKeywords:
    def test_chinese_refusal(self):
        assert check_response_keywords("我无法回答该问题") is True

    def test_chinese_refusal_sensitive(self):
        assert check_response_keywords("该内容涉及敏感话题") is True

    def test_english_refusal(self):
        assert check_response_keywords("I cannot assist with that request") is True

    def test_english_refusal_policy(self):
        assert check_response_keywords("This violates our content policy") is True

    def test_normal_chinese(self):
        assert check_response_keywords("Python 是一种通用编程语言") is False

    def test_normal_english(self):
        assert check_response_keywords("Here is the code you requested") is False

    def test_empty_content(self):
        assert check_response_keywords("") is False

    def test_custom_keywords(self):
        assert check_response_keywords("自定义拒绝", extra_keywords=["自定义拒绝"]) is True

    def test_custom_keywords_no_match(self):
        assert check_response_keywords("正常内容", extra_keywords=["自定义拒绝"]) is False


class TestDetectRefusal:
    def test_structured_signal_takes_priority(self):
        data = {"choices": [{"finish_reason": "content_filter", "message": {"content": "hello"}}]}
        assert detect_refusal(data) is True

    def test_keyword_detection_on_normal_finish(self):
        data = {"choices": [{"finish_reason": "stop", "message": {"content": "我无法回答该问题"}}]}
        assert detect_refusal(data) is True

    def test_normal_response(self):
        data = {"choices": [{"finish_reason": "stop", "message": {"content": "Python is great"}}]}
        assert detect_refusal(data) is False

    def test_custom_detector_overrides(self):
        data = {"choices": [{"finish_reason": "stop", "message": {"content": "short"}}]}

        def custom(ctx: RefusalContext) -> bool:
            return len(ctx.content) < 10

        assert detect_refusal(data, custom_detector=custom, model="test", provider="test") is True

    def test_custom_detector_exception_is_caught(self):
        data = {"choices": [{"finish_reason": "stop", "message": {"content": "hello world"}}]}

        def broken(ctx: RefusalContext) -> bool:
            raise ValueError("boom")

        assert detect_refusal(data, custom_detector=broken, model="test", provider="test") is False

    def test_extra_keywords(self):
        data = {"choices": [{"finish_reason": "stop", "message": {"content": "自定义拒绝词"}}]}
        assert detect_refusal(data, extra_keywords=["自定义拒绝词"]) is True
