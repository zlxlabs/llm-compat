# llm-compat

轻量 Python 包，抹平 OpenAI 兼容 API 的多 provider 差异。专为 New API 代理 + 多模型切换场景设计。

## 解决什么问题

10+ 个 Python 项目通过 New API（OpenAI 兼容代理）接入 DeepSeek、GPT、Gemini 等模型，切换模型时：

- `thinking` / `reasoning_effort` 参数各家写法不同，静默忽略不报错
- JSON 输出格式不一致（code fence、bare list），结构化输出各 provider 支持不同
- 重试、日志、超时每个项目都在重复写
- 国内模型因内容审查拒绝回答，需要手动切换到海外模型

llm-compat 统一处理这些差异，业务代码只需改配置不改代码。

## 功能概览

| 功能 | 说明 |
|------|------|
| **统一对话 API** | `chat()` / `chat_json()` / `chat_stream()` / `chat_image()` + 同步版本 |
| **结构化 JSON 输出** | 自动选择 json_schema 或 json_object 模式，Pydantic 校验，self-correction |
| **内容审查降级** | 国内模型被拒时自动切换海外模型，`chat()` 和 `chat_json()` 均支持 |
| **调用轨迹** | 成功和失败共享不可变 `CallTrace`，区分路由决策、真实模型尝试与终态 |
| **Provider 翻译** | reasoning_effort 跨 10 个 provider 族自动翻译 |
| **智能重试** | 指数退避 + jitter，错误分类（可重试/致命/超时） |
| **并发控制** | `max_concurrency` 参数，防止打爆 API |
| **生命周期 Hook** | `on_success` / `on_error` / `pre_request`，可接入熔断器 |
| **敏感词积累** | Collector sidecar 服务，跨项目收集拒绝事件，闭环回 pre-scan |

## 安装

```bash
uv add git+https://github.com/zj1123581321/llm-compat.git

# 启用敏感词前置检测（可选）
uv add "git+https://github.com/zj1123581321/llm-compat.git[sensitive]"
```

## 快速开始

```python
from llm_compat import LLMClient
from pydantic import BaseModel

class TagResult(BaseModel):
    tags: list[str]

async with LLMClient(
    base_url="https://your-newapi.com/v1",
    api_key="sk-xxx",
    content_fallbacks={
        "deepseek-v4-pro": ["gemini-3-flash-preview", "gemini-2.5-flash"],
    },
) as client:
    # 文本对话
    result = await client.chat("deepseek-v4-flash", messages, reasoning_effort="high")

    # 结构化 JSON（自动适配 provider 能力 + 内容审查降级）
    result = await client.chat_json("deepseek-v4-pro", messages, schema=TagResult)
    print(result.parsed)       # TagResult(tags=[...])
    print(result.fallback_from) # 降级时显示原始模型，否则 None
    print(result.trace.to_dict()) # 安全、可序列化的模型级调用事实
```

失败也使用同一套轨迹，不需要把 HTTP 错误猜成 JSON 解析失败：

```python
from llm_compat import LLMCallError

try:
    result = await client.chat_json("deepseek-v4-pro", messages, schema=TagResult)
except LLMCallError as error:
    print(error.error_kind, error.http_status)
    print(error.trace.to_dict() if error.trace else None)
```

`requested_model` 是调用方请求的模型；被预检跳过的模型只出现在
`route_decisions`；真正发出请求的模型才出现在 `model_attempts`；`final_model`
是成功模型或最后失败模型；`final_outcome` 是整个逻辑调用的结果。一次 attempt 的
`response_received` 只表示上游返回成功，不代表后续 JSON 解析成功。

## 文档

- **[接入指南](docs/guides/integration-guide.md)** — 完整 API 文档 + 配置参考 + 迁移指南
- **[RFC: 功能扩展](docs/rfcs/structured-output-and-extensions.md)** — 设计决策与路线图

## 包结构

```
src/llm_compat/
├── _base.py          — 分层 orchestrator（content fallback + JSON 编排）+ hooks
├── client.py         — async client
├── sync.py           — sync client
├── providers.py      — 10 族检测 + thinking 翻译 + json_mode
├── retry.py          — 智能重试 + 错误分类
├── refusal.py        — 3 层拒绝检测
├── fallback.py       — fallback 链解析 + 模态过滤
├── sensitive.py      — 前置敏感词检测（可选依赖）
├── json_utils.py     — JSON 清洗 + Pydantic 校验 + Schema 转换
├── errors.py         — 错误层级
├── _trace.py         — 不可变 CallTrace / RouteDecision / ModelAttempt
├── _types.py         — ChatResult, TokenUsage, LLMStats
├── _collector.py     — Collector 服务客户端
└── _compat.py        — 配置校验

collector/            — Sidecar 服务（FastAPI + SQLite）
tests/                360 tests
```

## 依赖

- `httpx>=0.27` — HTTP 客户端
- `pydantic>=2.0` — JSON 校验

可选：`pyahocorasick>=2.0` — 高性能敏感词检测（`pip install llm-compat[sensitive]`）

## License

MIT
