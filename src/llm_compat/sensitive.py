from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    import ahocorasick

    _HAS_AHOCORASICK = True
except ImportError:
    _HAS_AHOCORASICK = False


class SensitiveDetector:
    def __init__(self, words: list[str]) -> None:
        self._words = words
        if _HAS_AHOCORASICK and words:
            self._automaton = ahocorasick.Automaton()
            for word in words:
                self._automaton.add_word(word.lower(), word)
            self._automaton.make_automaton()
        else:
            self._automaton = None

    @property
    def is_available(self) -> bool:
        return len(self._words) > 0

    def contains(self, text: str) -> bool:
        if not text or not self._words:
            return False
        text_lower = text.lower()
        if self._automaton is not None:
            for _ in self._automaton.iter(text_lower):
                return True
            return False
        return any(w.lower() in text_lower for w in self._words)

    def contains_any(self, texts: list[str]) -> bool:
        return any(self.contains(t) for t in texts)

    def detect(self, text: str) -> list[str]:
        if not text or not self._words:
            return []
        text_lower = text.lower()
        if self._automaton is not None:
            return list({word for _, word in self._automaton.iter(text_lower)})
        return [w for w in self._words if w.lower() in text_lower]
