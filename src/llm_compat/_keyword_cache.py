from __future__ import annotations

import logging
import threading
import time

import httpx

logger = logging.getLogger(__name__)

_keyword_cache: dict[str, list[str]] = {}
_polling_urls: set[str] = set()
_poll_thread_started = False
_POLL_INTERVAL = 300.0


def _fetch_words_from_url(url: str) -> list[str] | None:
    try:
        with httpx.Client(timeout=5.0) as http:
            resp = http.get(url)
            return resp.json().get("words", [])
    except Exception:
        logger.debug("Failed to fetch keywords from %s", url)
        return None


def _refresh_url(url: str) -> None:
    words = _fetch_words_from_url(url)
    if words is not None:
        _keyword_cache[url] = words


def _poll_loop() -> None:
    while True:
        time.sleep(_POLL_INTERVAL)
        for url in list(_polling_urls):
            _refresh_url(url)


def _ensure_polling() -> None:
    global _poll_thread_started
    if not _poll_thread_started:
        _poll_thread_started = True
        t = threading.Thread(target=_poll_loop, daemon=True)
        t.start()


def get_cached_keywords(url: str) -> list[str]:
    if url not in _keyword_cache:
        words = _fetch_words_from_url(url)
        _keyword_cache[url] = words if words is not None else []

    _polling_urls.add(url)
    _ensure_polling()
    return list(_keyword_cache.get(url, []))
