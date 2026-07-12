from __future__ import annotations

import pytest

collect_ignore = ["integration_test.py"]


@pytest.fixture(autouse=True)
def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy_vars = (
        "http_proxy", "https_proxy", "all_proxy",
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    )
    for var in proxy_vars:
        monkeypatch.delenv(var, raising=False)
