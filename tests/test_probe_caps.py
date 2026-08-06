"""Tests for the conservative, fully mocked provider caps probe."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

sys.path.insert(0, str(Path(__file__).parents[1]))

from scripts import probe_caps  # noqa: E402


def _completion_body(reasoning_tokens: int | None, *, include_reasoning: bool = True) -> dict:
    details = {"reasoning_tokens": reasoning_tokens} if include_reasoning else {}
    return {
        "choices": [{"message": {"role": "assistant", "content": "answer"}}],
        "usage": {"completion_tokens_details": details},
    }


def _observation(
    *,
    status_code: int | None = 200,
    reasoning_tokens: int | None = 12,
    field_error: bool = False,
    error_kind: str | None = None,
    attempts: int = 1,
) -> probe_caps.RequestObservation:
    return probe_caps.RequestObservation(
        status_code=status_code,
        reasoning_tokens=reasoning_tokens,
        field_error=field_error,
        error_kind=error_kind,
        attempts=attempts,
    )


def _classify(
    field: probe_caps.ProbeField,
    *,
    control: probe_caps.RequestObservation | None = None,
    first: probe_caps.RequestObservation | None = None,
    second: probe_caps.RequestObservation | None = None,
) -> probe_caps.ProbeOutcome:
    return probe_caps._classify_outcome(
        model="deepseek-v4-flash",
        family="deepseek",
        field=field,
        control=control or _observation(reasoning_tokens=12),
        samples=(
            first or _observation(reasoning_tokens=0),
            second or _observation(reasoning_tokens=0),
        ),
    )


def _complete_probe(
    *,
    control: probe_caps.RequestObservation | None = None,
    replacement: dict[str, probe_caps.ProbeOutcome] | None = None,
) -> probe_caps.ModelProbe:
    outcomes = [
        _classify(field)
        for field in probe_caps.PROBE_FIELDS
    ]
    if replacement:
        outcomes = [replacement.get(outcome.field, outcome) for outcome in outcomes]
    return probe_caps.ModelProbe(
        model="deepseek-v4-flash",
        family="deepseek",
        control=control or _observation(reasoning_tokens=12),
        outcomes=tuple(outcomes),
    )


class TestThreeStateMatrix:
    @pytest.mark.parametrize(
        "field",
        probe_caps.PROBE_FIELDS,
        ids=lambda field: field.name,
    )
    def test_every_probe_field_uses_positive_control_and_repeated_samples(
        self, field: probe_caps.ProbeField
    ) -> None:
        outcome = _classify(field)

        assert outcome.field == field.name
        assert outcome.state == "supported"
        assert outcome.thinking_disabled is True

    def test_200_control_positive_and_target_zero_is_supported_and_generates_caps(self) -> None:
        probe = _complete_probe()
        outcome = probe.outcomes[0]

        assert outcome.state == "supported"
        assert outcome.thinking_disabled is True
        candidate = probe_caps._caps_fragment(probe)
        assert candidate is not None
        assert '"disable_mode": "native"' in candidate[0]
        assert '"efforts": frozenset({' in candidate[0]

    def test_200_control_positive_and_target_positive_is_supported_not_unsupported(self) -> None:
        outcome = _classify(
            probe_caps.PROBE_FIELDS[1],
            first=_observation(reasoning_tokens=8),
            second=_observation(reasoning_tokens=9),
        )

        assert outcome.state == "supported"
        assert outcome.thinking_disabled is False
        assert "未关闭思考" in outcome.detail

    def test_200_control_zero_is_inconclusive_and_no_caps_fragment(self) -> None:
        probe = _complete_probe(control=_observation(reasoning_tokens=0))
        outcome = _classify(
            probe_caps.PROBE_FIELDS[0],
            control=_observation(reasoning_tokens=0),
        )

        assert outcome.state == "inconclusive"
        assert probe_caps._caps_fragment(probe) is None

    def test_200_without_reasoning_tokens_is_inconclusive_and_no_caps_fragment(self) -> None:
        missing = _observation(reasoning_tokens=None)
        outcome = _classify(
            probe_caps.PROBE_FIELDS[0],
            first=missing,
            second=missing,
        )

        assert outcome.state == "inconclusive"
        incomplete = _complete_probe(replacement={outcome.field: outcome})
        assert probe_caps._caps_fragment(incomplete) is None

    def test_400_explicit_field_error_is_unsupported_and_safe_for_caps(self) -> None:
        unsupported = _classify(
            probe_caps.PROBE_FIELDS[0],
            first=_observation(status_code=400, reasoning_tokens=None, field_error=True),
            second=_observation(status_code=400, reasoning_tokens=None, field_error=True),
        )

        assert unsupported.state == "unsupported"
        assert unsupported.thinking_disabled is None
        complete = _complete_probe(replacement={unsupported.field: unsupported})
        candidate = probe_caps._caps_fragment(complete)
        assert candidate is not None
        assert '"minimal"' not in candidate[0]

    def test_400_other_error_is_inconclusive_and_no_caps_fragment(self) -> None:
        outcome = _classify(
            probe_caps.PROBE_FIELDS[0],
            first=_observation(status_code=400, reasoning_tokens=None),
            second=_observation(status_code=400, reasoning_tokens=None),
        )

        assert outcome.state == "inconclusive"
        incomplete = _complete_probe(replacement={outcome.field: outcome})
        assert probe_caps._caps_fragment(incomplete) is None

    def test_inconsistent_repeated_zero_evidence_is_inconclusive(self) -> None:
        outcome = _classify(
            probe_caps.PROBE_FIELDS[0],
            first=_observation(reasoning_tokens=0),
            second=_observation(reasoning_tokens=4),
        )

        assert outcome.state == "inconclusive"
        assert outcome.thinking_disabled is None

    @pytest.mark.parametrize("status_code", [429, 500, 503])
    def test_transient_http_status_is_inconclusive_after_two_retries(
        self, httpx_mock: HTTPXMock, status_code: int
    ) -> None:
        for _ in range(3):
            httpx_mock.add_response(status_code=status_code)
        runner = probe_caps.ProbeRunner(
            base_url="https://gateway.example/v1",
            api_key="test-key-only",
            models=["deepseek-v4-flash"],
            delay=0,
            backoff=0,
        )

        async def run_request() -> probe_caps.RequestObservation:
            async with httpx.AsyncClient(timeout=1) as client:
                return await runner._request(
                    client,
                    probe_caps.PROBE_FIELDS[0],
                    runner._payload("deepseek-v4-flash", probe_caps.PROBE_FIELDS[0].payload),
                )

        result = asyncio.run(run_request())
        assert result.status_code == status_code
        assert result.attempts == 3
        assert result.reasoning_tokens is None
        outcome = _classify(
            probe_caps.PROBE_FIELDS[0],
            first=result,
            second=result,
        )
        assert outcome.state == "inconclusive"

    def test_timeout_is_inconclusive_after_two_retries(self, httpx_mock: HTTPXMock) -> None:
        for _ in range(3):
            httpx_mock.add_exception(httpx.ReadTimeout("timed out"))
        runner = probe_caps.ProbeRunner(
            base_url="https://gateway.example/v1",
            api_key="test-key-only",
            models=["deepseek-v4-flash"],
            delay=0,
            backoff=0,
        )

        async def run_request() -> probe_caps.RequestObservation:
            async with httpx.AsyncClient(timeout=1) as client:
                return await runner._request(
                    client,
                    probe_caps.PROBE_FIELDS[0],
                    runner._payload("deepseek-v4-flash", probe_caps.PROBE_FIELDS[0].payload),
                )

        result = asyncio.run(run_request())
        assert result.error_kind == "timeout"
        assert result.attempts == 3
        assert _classify(probe_caps.PROBE_FIELDS[0], first=result, second=result).state == (
            "inconclusive"
        )


class TestResponseParsing:
    def test_explicit_field_error_is_recognized_without_echoing_body(self) -> None:
        field = probe_caps.PROBE_FIELDS[0]
        response = httpx.Response(
            400,
            json={
                "error": {
                    "message": "Unrecognized request argument supplied: reasoning_effort"
                }
            },
        )

        observation = probe_caps._response_observation(response, field)

        assert observation.field_error is True
        assert observation.status_code == 400

    def test_400_message_about_another_field_is_not_unsupported(self) -> None:
        field = probe_caps.PROBE_FIELDS[0]
        response = httpx.Response(
            400,
            json={"error": {"message": "Unrecognized request argument supplied: temperature"}},
        )

        assert probe_caps._response_observation(response, field).field_error is False

    def test_model_error_that_mentions_field_list_is_not_field_rejection(self) -> None:
        field = probe_caps.PROBE_FIELDS[0]
        response = httpx.Response(
            400,
            json={
                "error": {
                    "message": (
                        "Invalid model name; supported request fields include reasoning_effort"
                    )
                }
            },
        )

        observation = probe_caps._response_observation(response, field)
        outcome = _classify(field, first=observation, second=observation)

        assert observation.field_error is False
        assert outcome.state == "inconclusive"


class TestCredentialsAndRequests:
    def test_environment_key_runs_and_key_or_full_url_never_enters_report(
        self,
        httpx_mock: HTTPXMock,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        secret = "test-key-value-not-secret"
        monkeypatch.setenv("LLM_API_KEY", secret)
        for index in range(1 + len(probe_caps.PROBE_FIELDS) * 2):
            httpx_mock.add_response(
                json=_completion_body(12 if index == 0 else 0),
            )

        exit_code = probe_caps.main(
            [
                "--base-url",
                "https://private-gateway.example/v1",
                "--model",
                "deepseek-v4-flash",
                "--delay",
                "0",
                "--timeout",
                "1",
            ]
        )
        output = capsys.readouterr().out

        assert exit_code == 0
        assert "private-gateway.example" in output
        assert "https://private-gateway.example/v1" not in output
        assert secret not in output
        assert "| `deepseek` |" in output
        assert '"deepseek": {' in output
        assert "ProviderDetection" not in output
        requests = httpx_mock.get_requests()
        assert requests[0].headers["authorization"] == f"Bearer {secret}"
        bodies = [json.loads(request.content) for request in requests]
        assert "reasoning_effort" not in bodies[0]
        assert "thinking" not in bodies[0]
        assert {body.get("reasoning_effort") for body in bodies[1:]} >= {
            "minimal",
            "low",
            "medium",
            "high",
            "max",
            "xhigh",
            "none",
            "disabled",
        }
        assert any(body.get("thinking") == {"type": "disabled"} for body in bodies[1:])

    def test_missing_environment_key_exits_without_sending_request(
        self,
        httpx_mock: HTTPXMock,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.delenv("LLM_API_KEY", raising=False)

        exit_code = probe_caps.main(
            ["--base-url", "https://gateway.example/v1", "--model", "deepseek-v4-flash"]
        )

        assert exit_code == 2
        assert "LLM_API_KEY" in capsys.readouterr().err
        assert not httpx_mock.get_requests()

    def test_inline_api_key_is_rejected_without_echoing_value(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("LLM_API_KEY", "test-env-only")
        inline_secret = "test-inline-must-not-be-accepted"

        exit_code = probe_caps.main(
            [
                "--base-url",
                "https://gateway.example/v1",
                "--model",
                "deepseek-v4-flash",
                "--api-key",
                inline_secret,
            ]
        )

        captured = capsys.readouterr()
        assert exit_code == 2
        assert "命令行 API key" in captured.err
        assert inline_secret not in captured.err

    def test_base_url_userinfo_is_rejected_without_network_request(
        self,
        httpx_mock: HTTPXMock,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("LLM_API_KEY", "test-env-only")

        exit_code = probe_caps.main(
            [
                "--base-url",
                "https://user:password@gateway.example/v1",
                "--model",
                "deepseek-v4-flash",
            ]
        )

        captured = capsys.readouterr()
        assert exit_code == 2
        assert "userinfo" in captured.err
        assert "user:password" not in captured.err
        assert "password" not in captured.err
        assert not httpx_mock.get_requests()

    def test_help_is_available_without_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        with pytest.raises(SystemExit) as raised:
            probe_caps.main(["--help"])
        assert raised.value.code == 0


def test_report_has_no_caps_fragment_when_all_cells_are_inconclusive() -> None:
    invalid_control = _observation(
        status_code=None,
        reasoning_tokens=None,
        error_kind="network_error",
    )
    inconclusive_outcomes = {
        field.name: _classify(
            field,
            control=invalid_control,
            first=invalid_control,
            second=invalid_control,
        )
        for field in probe_caps.PROBE_FIELDS
    }
    model_probe = _complete_probe(control=invalid_control, replacement=inconclusive_outcomes)
    report = probe_caps.ProbeReport(
        target_host="127.0.0.1",
        model_probes=(model_probe,),
        generated_at="test",
    )

    rendered = probe_caps.render_report(report)

    assert report.has_inconclusive is True
    assert "全部格子" not in rendered
    assert "没有生成任何 caps 片段" in rendered
    assert "```python" not in rendered
