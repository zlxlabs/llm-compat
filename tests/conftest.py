from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(var, raising=False)
