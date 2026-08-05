"""Probe reasoning fields accepted by an OpenAI-compatible gateway.

The probe is deliberately conservative: a request which does not provide
enough evidence is reported as ``inconclusive`` and never becomes a caps
fragment.  The script only reads ``LLM_API_KEY`` from the environment and
never writes provider source files.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

import httpx

from llm_compat.providers import detect_provider  # type: ignore[import-untyped]

ProbeState = Literal["supported", "unsupported", "inconclusive"]

EFFORT_VALUES: tuple[str, ...] = (
    "minimal",
    "low",
    "medium",
    "high",
    "max",
    "xhigh",
)

DEFAULT_PROMPT = (
    "Solve this arithmetic problem carefully. Compute 8473 * 923, show enough reasoning "
    "to make the result auditable, and end with the final number."
)


@dataclass(frozen=True)
class ProbeField:
    """One request-field variant tested against a model."""

    name: str
    payload: dict[str, object]


PROBE_FIELDS: tuple[ProbeField, ...] = (
    *(
        ProbeField(f"reasoning_effort={effort}", {"reasoning_effort": effort})
        for effort in EFFORT_VALUES
    ),
    ProbeField("reasoning_effort=none", {"reasoning_effort": "none"}),
    ProbeField("reasoning_effort=disabled", {"reasoning_effort": "disabled"}),
    ProbeField("thinking={type:disabled}", {"thinking": {"type": "disabled"}}),
)


@dataclass(frozen=True)
class RequestObservation:
    """Safe, reportable facts from one HTTP request."""

    status_code: int | None
    reasoning_tokens: int | None
    field_error: bool = False
    error_kind: str | None = None
    attempts: int = 1


@dataclass(frozen=True)
class ProbeOutcome:
    """Classification for one field after two samples and one control."""

    model: str
    family: str
    field: str
    state: ProbeState
    status_codes: tuple[int | None, int | None]
    reasoning_tokens: tuple[int | None, int | None]
    attempts: tuple[int, int]
    thinking_disabled: bool | None
    detail: str


@dataclass(frozen=True)
class ModelProbe:
    model: str
    family: str
    control: RequestObservation
    outcomes: tuple[ProbeOutcome, ...]


@dataclass(frozen=True)
class ProbeReport:
    target_host: str
    model_probes: tuple[ModelProbe, ...]
    generated_at: str

    @property
    def has_inconclusive(self) -> bool:
        for probe in self.model_probes:
            if not _baseline_is_valid(probe.control):
                return True
            if any(outcome.state == "inconclusive" for outcome in probe.outcomes):
                return True
        return False


class ProbeConfigError(ValueError):
    """Raised when the probe cannot start safely."""


def _non_negative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("必须是非负数字") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("必须是非负数字")
    return parsed


def _positive_float(value: str) -> float:
    parsed = _non_negative_float(value)
    if parsed == 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "对 OpenAI 兼容网关实测 reasoning_effort 与 thinking 字段。"
            "每个字段发送 2 次样本，并以不带字段的对照请求验证 reasoning_tokens；"
            "只有证据充分的结果才会生成 caps 片段。"
        )
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="网关基础 URL（也可用 LLM_BASE_URL 环境变量；不会写入报告）",
    )
    parser.add_argument(
        "--model",
        dest="model_args",
        action="append",
        default=[],
        help="要探测的模型名；可重复传入",
    )
    parser.add_argument(
        "--models",
        default="",
        help="逗号分隔的模型名（可与多个 --model 混用）",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="所有对照和样本请求共用的 prompt（默认是需要实际推理的算术题）",
    )
    parser.add_argument(
        "--delay",
        type=_non_negative_float,
        default=0.5,
        help="请求启动之间的最小间隔，单位秒（默认 0.5；测试可设为 0）",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_float,
        default=20.0,
        help="单次 HTTP 请求超时，单位秒（默认 20）",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        choices=(1, 2),
        default=2,
        help="并发上限，只允许 1 或 2（默认 2）",
    )
    return parser


def _reject_inline_api_key(argv: Sequence[str]) -> None:
    for argument in argv:
        if argument == "--api-key" or argument.startswith("--api-key="):
            raise ProbeConfigError(
                "拒绝命令行 API key；请只通过 LLM_API_KEY 环境变量提供凭据"
            )


def _target_host(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProbeConfigError("base URL 必须是带主机名的 http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ProbeConfigError("base URL 不得包含 userinfo；请只通过 LLM_API_KEY 提供凭据")
    return parsed.hostname


def _chat_endpoint(base_url: str) -> str:
    _target_host(base_url)
    parsed = urlsplit(base_url)
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        endpoint_path = path
    else:
        endpoint_path = f"{path}/chat/completions"
    return parsed._replace(path=endpoint_path, query="", fragment="").geturl()


def _collect_models(model_args: Sequence[str], models_arg: str) -> tuple[str, ...]:
    models: list[str] = []
    for model in (*model_args, *models_arg.split(",")):
        normalized = model.strip()
        if normalized and normalized not in models:
            models.append(normalized)
    if not models:
        raise ProbeConfigError("至少提供一个 --model 或 --models")
    return tuple(models)


def _extract_reasoning_tokens(body: object) -> int | None:
    if not isinstance(body, dict):
        return None
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return None
    details = usage.get("completion_tokens_details")
    if not isinstance(details, dict):
        return None
    value = details.get("reasoning_tokens")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


_FIELD_ERROR_WORDS = re.compile(
    r"unrecognized|unrecognised|not recognized|not recognised|unknown|unsupported|"
    r"not supported|not permitted|"
    r"unexpected|invalid|not allowed|additional propert(?:y|ies)|extra fields|"
    r"does not accept|must be one of",
    re.IGNORECASE,
)


def _field_name(field: ProbeField) -> str:
    return field.name.split("=", 1)[0]


def _error_points_to_field(response: httpx.Response, field: ProbeField) -> bool:
    """Return true only when a 400 can be attributed to this field."""
    field_name = _field_name(field)
    message = response.text
    param: object | None = None
    try:
        body: object = response.json()
    except ValueError:
        body = None

    if isinstance(body, dict):
        error = body.get("error")
        error_data = error if isinstance(error, dict) else body
        param = error_data.get("param")
        message_value = error_data.get("message")
        if isinstance(message_value, str):
            message = message_value

    if isinstance(param, str):
        return param == field_name

    field_name_lower = field_name.casefold()
    clauses = re.split(r"[;.!?\n]+", message.casefold())
    return any(
        field_name_lower in clause and _FIELD_ERROR_WORDS.search(clause) is not None
        for clause in clauses
    )


def _response_observation(response: httpx.Response, field: ProbeField) -> RequestObservation:
    if response.status_code == 200:
        try:
            body: object = response.json()
        except ValueError:
            body = None
        return RequestObservation(
            status_code=200,
            reasoning_tokens=_extract_reasoning_tokens(body),
        )
    return RequestObservation(
        status_code=response.status_code,
        reasoning_tokens=None,
        field_error=response.status_code == 400 and _error_points_to_field(response, field),
        error_kind=f"http_{response.status_code}",
    )


class _RequestGate:
    """Serialize request starts enough to enforce a global minimum interval."""

    def __init__(self, interval: float) -> None:
        self._interval = interval
        self._lock = asyncio.Lock()
        self._last_start = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            remaining = self._interval - (now - self._last_start)
            if remaining > 0:
                await asyncio.sleep(remaining)
            self._last_start = time.monotonic()


class ProbeRunner:
    """Run the bounded, retrying probe against one gateway."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        models: Sequence[str],
        prompt: str = DEFAULT_PROMPT,
        delay: float = 0.5,
        timeout: float = 20.0,
        max_concurrency: int = 2,
        max_retries: int = 2,
        backoff: float = 0.5,
    ) -> None:
        if max_concurrency not in {1, 2}:
            raise ProbeConfigError("并发上限只能是 1 或 2")
        if max_retries < 0:
            raise ProbeConfigError("重试次数不能为负数")
        if not api_key.strip():
            raise ProbeConfigError("缺少 LLM_API_KEY 环境变量，拒绝在无凭据时发请求")
        self._target_host = _target_host(base_url)
        self._endpoint = _chat_endpoint(base_url)
        self._api_key = api_key
        self._models = tuple(models)
        self._prompt = prompt
        self._timeout = timeout
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._max_retries = max_retries
        self._backoff = backoff
        self._gate = _RequestGate(delay)

    def _payload(self, model: str, overlay: dict[str, object] | None = None) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": model,
            "messages": [{"role": "user", "content": self._prompt}],
        }
        if overlay:
            payload.update(overlay)
        return payload

    @staticmethod
    def _should_retry(observation: RequestObservation) -> bool:
        return observation.status_code == 429 or (
            observation.status_code is not None and 500 <= observation.status_code <= 599
        ) or observation.error_kind in {"timeout", "network_error"}

    async def _request(
        self,
        client: httpx.AsyncClient,
        field: ProbeField,
        payload: dict[str, object],
    ) -> RequestObservation:
        last_observation: RequestObservation | None = None
        for retry_index in range(self._max_retries + 1):
            async with self._semaphore:
                await self._gate.wait()
                try:
                    response = await client.post(self._endpoint, json=payload)
                    observation = _response_observation(response, field)
                except httpx.TimeoutException:
                    observation = RequestObservation(
                        status_code=None,
                        reasoning_tokens=None,
                        error_kind="timeout",
                    )
                except httpx.RequestError:
                    observation = RequestObservation(
                        status_code=None,
                        reasoning_tokens=None,
                        error_kind="network_error",
                    )

            last_observation = RequestObservation(
                status_code=observation.status_code,
                reasoning_tokens=observation.reasoning_tokens,
                field_error=observation.field_error,
                error_kind=observation.error_kind,
                attempts=retry_index + 1,
            )
            if not self._should_retry(observation) or retry_index >= self._max_retries:
                return last_observation
            if self._backoff:
                await asyncio.sleep(self._backoff * (2**retry_index))

        raise RuntimeError("unreachable retry loop")

    async def _probe_model(self, client: httpx.AsyncClient, model: str) -> ModelProbe:
        family = detect_provider(model).family
        control_field = ProbeField("control", {})
        control = await self._request(client, control_field, self._payload(model))

        outcomes: list[ProbeOutcome] = []
        for field in PROBE_FIELDS:
            samples = await asyncio.gather(
                self._request(client, field, self._payload(model, field.payload)),
                self._request(client, field, self._payload(model, field.payload)),
            )
            outcomes.append(
                _classify_outcome(
                    model=model,
                    family=family,
                    field=field,
                    control=control,
                    samples=(samples[0], samples[1]),
                )
            )
        return ModelProbe(model=model, family=family, control=control, outcomes=tuple(outcomes))

    async def run(self) -> ProbeReport:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
            model_probes = await asyncio.gather(
                *(self._probe_model(client, model) for model in self._models)
            )
        return ProbeReport(
            target_host=self._target_host,
            model_probes=tuple(model_probes),
            generated_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )


def _baseline_is_valid(control: RequestObservation) -> bool:
    return (
        control.status_code == 200
        and control.reasoning_tokens is not None
        and control.reasoning_tokens > 0
    )


def _status_codes(
    observations: tuple[RequestObservation, RequestObservation],
) -> tuple[int | None, int | None]:
    return observations[0].status_code, observations[1].status_code


def _classify_outcome(
    *,
    model: str,
    family: str,
    field: ProbeField,
    control: RequestObservation,
    samples: tuple[RequestObservation, RequestObservation],
) -> ProbeOutcome:
    statuses = _status_codes(samples)
    reasoning = (samples[0].reasoning_tokens, samples[1].reasoning_tokens)
    attempts = (samples[0].attempts, samples[1].attempts)

    if all(sample.status_code == 400 and sample.field_error for sample in samples):
        return ProbeOutcome(
            model=model,
            family=family,
            field=field.name,
            state="unsupported",
            status_codes=statuses,
            reasoning_tokens=reasoning,
            attempts=attempts,
            thinking_disabled=None,
            detail="两次 HTTP 400 都明确指向该字段或该值",
        )

    if not all(sample.status_code == 200 for sample in samples):
        return ProbeOutcome(
            model=model,
            family=family,
            field=field.name,
            state="inconclusive",
            status_codes=statuses,
            reasoning_tokens=reasoning,
            attempts=attempts,
            thinking_disabled=None,
            detail="HTTP 结果不稳定或不是可判定的字段拒绝",
        )

    if not all(sample.reasoning_tokens is not None for sample in samples):
        return ProbeOutcome(
            model=model,
            family=family,
            field=field.name,
            state="inconclusive",
            status_codes=statuses,
            reasoning_tokens=reasoning,
            attempts=attempts,
            thinking_disabled=None,
            detail="响应缺少 usage.completion_tokens_details.reasoning_tokens",
        )

    if not _baseline_is_valid(control):
        return ProbeOutcome(
            model=model,
            family=family,
            field=field.name,
            state="inconclusive",
            status_codes=statuses,
            reasoning_tokens=reasoning,
            attempts=attempts,
            thinking_disabled=None,
            detail="对照请求未证明模型默认会产生 reasoning_tokens",
        )

    disabled_flags = tuple(tokens == 0 for tokens in reasoning)
    if disabled_flags[0] != disabled_flags[1]:
        return ProbeOutcome(
            model=model,
            family=family,
            field=field.name,
            state="inconclusive",
            status_codes=statuses,
            reasoning_tokens=reasoning,
            attempts=attempts,
            thinking_disabled=None,
            detail="两次采样对 reasoning_tokens 是否为零的结论不一致",
        )

    is_disabled = disabled_flags[0]
    return ProbeOutcome(
        model=model,
        family=family,
        field=field.name,
        state="supported",
        status_codes=statuses,
        reasoning_tokens=reasoning,
        attempts=attempts,
        thinking_disabled=is_disabled,
        detail=(
            "字段接受，且已证明关闭思考"
            if is_disabled
            else "字段接受，但未关闭思考（不算 unsupported）"
        ),
    )


def _control_state(control: RequestObservation) -> ProbeState:
    return "supported" if _baseline_is_valid(control) else "inconclusive"


def _format_http_codes(codes: Sequence[int | None]) -> str:
    formatted = [str(code) if code is not None else "—" for code in codes]
    if len(set(formatted)) == 1:
        return formatted[0]
    return "/".join(formatted)


def _format_reasoning(values: Sequence[int | None]) -> str:
    formatted = [str(value) if value is not None else "—" for value in values]
    if len(set(formatted)) == 1:
        return formatted[0]
    return "/".join(formatted)


def _format_attempts(values: Sequence[int]) -> str:
    formatted = [str(value) for value in values]
    if len(set(formatted)) == 1:
        return formatted[0]
    return "/".join(formatted)


def _format_caps_set(efforts: Sequence[str]) -> str:
    if not efforts:
        return "frozenset()"
    values = ", ".join(f'"{effort}"' for effort in efforts)
    return f"frozenset({{{values}}})"


def _caps_fragment(probe: ModelProbe) -> tuple[str, str] | None:
    """Return a copyable partial caps entry only when every cell is determinate."""
    if not _baseline_is_valid(probe.control) or any(
        outcome.state == "inconclusive" for outcome in probe.outcomes
    ):
        return None

    by_field = {outcome.field: outcome for outcome in probe.outcomes}
    efforts = tuple(
        effort
        for effort in EFFORT_VALUES
        if by_field[f"reasoning_effort={effort}"].state == "supported"
    )

    disable_mode: str | None = None
    representable_closers = (
        ("thinking={type:disabled}", "native"),
        ("reasoning_effort=none", "effort_none"),
        ("reasoning_effort=minimal", "minimal_fallback"),
    )
    for field, mode in representable_closers:
        outcome = by_field[field]
        if outcome.state == "supported" and outcome.thinking_disabled is True:
            disable_mode = mode
            break

    if disable_mode is None:
        raw_closer = next(
            (
                outcome.field
                for outcome in probe.outcomes
                if outcome.state == "supported" and outcome.thinking_disabled is True
            ),
            None,
        )
        if raw_closer is not None:
            return (
                "",
                f"{probe.model} ({probe.family}) 有可关闭思考的原始字段 {raw_closer}，"
                "但当前 providers.py 没有可安全映射的 disable_mode；不生成 caps 片段。",
            )
        disable_mode = "unsupported"

    fragment = (
        f'"{probe.family}": {{\n'
        f'    "disable_mode": "{disable_mode}",\n'
        f'    "efforts": {_format_caps_set(efforts)},\n'
        "},"
    )
    return fragment, (
        f"{probe.model} ({probe.family}) 的候选片段；仅覆盖本探针实测的 disable_mode/efforts，"
        "请保留现有 supports_vision/json_mode 并人工审阅。"
    )


def render_report(report: ProbeReport) -> str:
    """Render a redacted Markdown report and safe, manually-reviewable snippets."""
    lines = [
        "# Provider caps 探针报告",
        "",
        f"- 目标主机：`{report.target_host}`",
        f"- 生成时间：`{report.generated_at}`",
        "- 每个字段采样：2 次；429/5xx/超时/网络错误最多额外重试 2 次",
        "- 判定：`supported` / `unsupported` / `inconclusive`",
        "- `inconclusive` 不生成 caps 片段，需修复网络/响应证据后重跑",
        "",
        "## Markdown 矩阵",
        "",
        "| 模型 | family | 字段 | 状态 | 网关接受 | HTTP | reasoning_tokens | "
        "思考已关闭 | 尝试次数 | 说明 |",
        "|---|---|---|---|---|---|---:|---|---:|---|",
    ]
    for probe in report.model_probes:
        control_state = _control_state(probe.control)
        control_detail = (
            "对照 reasoning_tokens > 0，可用于关闭思考佐证"
            if control_state == "supported"
            else "对照不可用；不能证明关闭思考"
        )
        lines.append(
            f"| `{probe.model}` | `{probe.family}` | `control` | `{control_state}` | "
            f"{'是' if control_state == 'supported' else '—'} | "
            f"{_format_http_codes((probe.control.status_code,))} | "
            f"{_format_reasoning((probe.control.reasoning_tokens,))} | — | "
            f"{probe.control.attempts} | {control_detail} |"
        )
        for outcome in probe.outcomes:
            accepted = (
                "是"
                if outcome.state == "supported"
                else "否"
                if outcome.state == "unsupported"
                else "—"
            )
            closed = (
                "是"
                if outcome.thinking_disabled is True
                else "否"
                if outcome.thinking_disabled is False
                else "—"
            )
            lines.append(
                f"| `{outcome.model}` | `{outcome.family}` | `{outcome.field}` | "
                f"`{outcome.state}` | "
                f"{accepted} | "
                f"{_format_http_codes(outcome.status_codes)} | "
                f"{_format_reasoning(outcome.reasoning_tokens)} | {closed} | "
                f"{_format_attempts(outcome.attempts)} | {outcome.detail} |"
            )

    lines.extend(["", "## 可复制的 caps 片段", ""])
    snippets = 0
    for probe in report.model_probes:
        candidate = _caps_fragment(probe)
        if candidate is None:
            lines.append(f"- `{probe.model}`：含 `inconclusive` 或无有效对照，不生成片段。")
            continue
        fragment, note = candidate
        if fragment:
            snippets += 1
            lines.extend(
                [
                    f"### {probe.model} ({probe.family})",
                    "",
                    "```python",
                    fragment,
                    "```",
                    "",
                    f"> {note}",
                    "",
                ]
            )
        else:
            lines.extend([f"- {note}", ""])
    if snippets == 0:
        lines.append(
            "本次没有生成任何 caps 片段；这是安全的保守结果，请按报告提示重跑或人工处理。"
        )
    return "\n".join(lines).rstrip() + "\n"


def _config_from_args(args: argparse.Namespace) -> tuple[str, str, tuple[str, ...]]:
    base_url = args.base_url or os.environ.get("LLM_BASE_URL", "")
    if not base_url:
        raise ProbeConfigError("缺少 --base-url 或 LLM_BASE_URL 环境变量")
    _target_host(base_url)
    api_key = os.environ.get("LLM_API_KEY", "")
    if not api_key.strip():
        raise ProbeConfigError("缺少 LLM_API_KEY 环境变量；脚本不会从命令行读取 key")
    models = _collect_models(args.model_args, args.models)
    return base_url, api_key, models


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = tuple(sys.argv[1:] if argv is None else argv)
    try:
        _reject_inline_api_key(raw_argv)
        args = _build_parser().parse_args(raw_argv)
        base_url, api_key, models = _config_from_args(args)
        runner = ProbeRunner(
            base_url=base_url,
            api_key=api_key,
            models=models,
            prompt=args.prompt,
            delay=args.delay,
            timeout=args.timeout,
            max_concurrency=args.max_concurrency,
        )
        report = asyncio.run(runner.run())
    except ProbeConfigError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    print(render_report(report), end="")
    return 1 if report.has_inconclusive else 0


if __name__ == "__main__":
    raise SystemExit(main())
