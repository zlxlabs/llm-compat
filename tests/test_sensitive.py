from __future__ import annotations

from llm_compat.sensitive import SensitiveDetector


class TestSensitiveDetector:
    def test_basic_detection(self):
        detector = SensitiveDetector(words=["敏感词", "违禁"])
        assert detector.contains("这段文字包含敏感词") is True

    def test_no_match(self):
        detector = SensitiveDetector(words=["敏感词"])
        assert detector.contains("这段文字完全正常") is False

    def test_empty_input(self):
        detector = SensitiveDetector(words=["敏感词"])
        assert detector.contains("") is False

    def test_empty_words(self):
        detector = SensitiveDetector(words=[])
        assert detector.contains("任何文字") is False

    def test_case_insensitive_english(self):
        detector = SensitiveDetector(words=["sensitive"])
        assert detector.contains("This is SENSITIVE content") is True

    def test_multiple_texts(self):
        detector = SensitiveDetector(words=["敏感词"])
        assert detector.contains_any(["正常文字", "包含敏感词的文字"]) is True

    def test_multiple_texts_no_match(self):
        detector = SensitiveDetector(words=["敏感词"])
        assert detector.contains_any(["正常1", "正常2"]) is False

    def test_is_available(self):
        detector = SensitiveDetector(words=["test"])
        assert detector.is_available is True

    def test_empty_words_not_available(self):
        detector = SensitiveDetector(words=[])
        assert detector.is_available is False

    def test_detect_returns_matched_words(self):
        detector = SensitiveDetector(words=["敏感词", "违禁", "正常"])
        matched = detector.detect("这段文字包含敏感词和违禁内容")
        assert "敏感词" in matched
        assert "违禁" in matched
        assert "正常" not in matched

    def test_words_property(self):
        detector = SensitiveDetector(words=["词1", "词2"])
        assert detector.words == ["词1", "词2"]

    def test_words_property_returns_copy(self):
        detector = SensitiveDetector(words=["词1"])
        words = detector.words
        words.append("词2")
        assert detector.words == ["词1"]

    def test_words_property_empty(self):
        detector = SensitiveDetector(words=[])
        assert detector.words == []
