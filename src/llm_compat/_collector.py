from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class CollectorClient:
    def __init__(
        self,
        url: str,
        project: str = "",
        preview_length: int = 200,
        cache_path: Path | str | None = None,
        http: Any = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._project = project
        self._preview_length = preview_length
        self._cache_path = Path(cache_path) if cache_path else None
        self._http = http
        self._words_hash: str = ""

    def _get_http(self) -> Any:
        if self._http is not None:
            return self._http
        if not hasattr(self, "_own_http") or self._own_http is None:
            self._own_http = httpx.AsyncClient(base_url=self._url, timeout=5.0)
        return self._own_http

    async def report_refusal(
        self,
        model: str,
        error_type: str,
        input_text: str = "",
        provider: str = "",
    ) -> None:
        body = {
            "model": model,
            "error_type": error_type,
            "input_preview": input_text[: self._preview_length],
            "source_project": self._project,
            "provider": provider,
        }
        try:
            http = self._get_http()
            await http.post(f"{self._url}/refusals", json=body)
        except Exception:
            logger.debug("Collector unavailable, caching refusal locally")
            self._write_cache(body)

    def _write_cache(self, record: dict[str, Any]) -> None:
        if self._cache_path is None:
            return
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            with self._cache_path.open("a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            logger.warning("Failed to write refusal cache", exc_info=True)

    async def fetch_words(self) -> list[str]:
        try:
            http = self._get_http()
            resp = await http.get(f"{self._url}/words")
            data = resp.json()
            self._words_hash = data.get("hash", "")
            return data.get("words", [])
        except Exception:
            logger.debug("Collector unavailable, returning empty word list")
            return []

    async def check_update(self) -> bool:
        try:
            http = self._get_http()
            resp = await http.get(f"{self._url}/words/hash")
            new_hash = resp.json().get("hash", "")
            if new_hash and new_hash != self._words_hash:
                self._words_hash = new_hash
                return True
            return False
        except Exception:
            return False

    def extract_preview(self, messages: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(item.get("text", ""))
        full = " ".join(parts)
        return full[: self._preview_length]

    async def close(self) -> None:
        if hasattr(self, "_own_http") and self._own_http is not None:
            await self._own_http.aclose()
            self._own_http = None
