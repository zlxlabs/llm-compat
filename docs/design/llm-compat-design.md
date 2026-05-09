# llm-compat 包设计

轻量 Python 包，抹平 OpenAI 兼容 API 的多 provider 差异。专为 **New API 代理 + 多模型切换** 场景设计。

## 定位

```
你的项目 → llm-compat → httpx → New API → DeepSeek / GPT / Gemini / ...
```

**只做三件事**：
1. Provider 差异翻译（thinking / reasoning_effort）
2. 响应兼容处理（JSON code fence / bare list）
3. 可靠传输（重试 / 超时 / 可观测日志）

**不做**：路由、鉴权、配置管理、Prompt 模板、业务 Schema。

## 包结构

```
llm-compat/
├── pyproject.toml
├── src/
│   └── llm_compat/
│       ├── __init__.py          # 公开 API：LLMClient, providers
│       ├── client.py            # 核心客户端（chat / chat_json / chat_stream / chat_image）
│       ├── providers.py         # Provider 检测 + thinking 翻译
│       ├── retry.py             # 指数退避重试
│       ├── json_utils.py        # JSON 响应清洗（code fence / bare list 包装）
│       └── _types.py            # 内部类型定义
└── tests/
    ├── test_providers.py        # Provider 翻译矩阵测试
    ├── test_client.py           # Client 集成测试（httpx mock）
    ├── test_retry.py            # 重试逻辑测试
    └── test_json_utils.py       # JSON 清洗测试
```

## 依赖

```toml
[project]
name = "llm-compat"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",
]

[project.optional-dependencies]
pydantic = ["pydantic>=2.0"]  # chat_json 的 schema 校验，可选
```

**零重依赖**：核心只依赖 httpx。Pydantic 作为可选依赖，不用 chat_json 就不需要装。

## 核心 API 设计

### 1. 客户端（client.py）

```python
from llm_compat import LLMClient

# 初始化：只需 base_url + api_key
client = LLMClient(
    base_url="https://your-newapi.com/v1",
    api_key="sk-xxx",
)

# --- 基础对话 ---
answer = await client.chat(
    model="deepseek-v4-flash",
    messages=[{"role": "user", "content": "hello"}],
    reasoning_effort="high",       # 可选，透传或翻译
)
# 返回: str

# --- 结构化 JSON ---
from pydantic import BaseModel

class TagResult(BaseModel):
    tags: list[str]

result = await client.chat_json(
    model="deepseek-v4-flash",
    messages=messages,
    schema=TagResult,
    reasoning_effort="disabled",   # 关闭思考，省 token
)
# 返回: TagResult 实例

# --- 流式 ---
async for chunk in client.chat_stream(
    model="gpt-4o",
    messages=messages,
):
    print(chunk, end="")

# --- 多模态图片 ---
answer = await client.chat_image(
    model="doubao-vision-pro",
    text="描述这张图",
    image_data=raw_bytes,
    media_type="image/png",
)

# --- 多图 ---
answer = await client.chat_images(
    model="doubao-vision-pro",
    text="对比这些截图",
    images=[(bytes1, "image/png"), (bytes2, "image/jpeg")],
)

# --- 清理 ---
await client.close()
```

**设计要点**：
- `model` 是每次调用的参数，不是客户端级别的 — 同一个 client 可以调不同模型
- `reasoning_effort` 在调用时传入，client 根据 model 名自动做 provider 翻译
- 返回值简洁：`chat` 返回 `str`，`chat_json` 返回 Pydantic 实例

### 2. Provider 翻译（providers.py）

```python
# --- Provider 检测 ---
from llm_compat.providers import detect_provider, build_thinking_params

detect_provider("deepseek-v4-flash")    # → "deepseek"
detect_provider("gpt-4o")              # → "openai"
detect_provider("gemini-2.5-flash")    # → "gemini"
detect_provider("qwen-plus")           # → "qwen"
detect_provider("unknown-model")       # → "generic"（透传，不翻译）

# --- Thinking 参数翻译 ---
# 输入统一的 reasoning_effort 值，输出 provider 特定的 payload 字段
build_thinking_params("deepseek", "disabled")
# → {"thinking": {"type": "disabled"}}

build_thinking_params("deepseek", "high")
# → {"reasoning_effort": "high"}

build_thinking_params("deepseek", "max")
# → {"reasoning_effort": "max"}

build_thinking_params("openai", "high")
# → {"reasoning_effort": "high"}

build_thinking_params("openai", "disabled")
# → {"reasoning_effort": "none"}

build_thinking_params("gemini", "disabled")
# → {"reasoning_effort": "none"}

build_thinking_params("generic", "high")
# → {"reasoning_effort": "high"}  # 透传

build_thinking_params("generic", "disabled")
# → {}  # 未知 provider 不知道怎么关，安全丢弃 + warn
```

**Provider 注册表**：

```python
_PROVIDER_PATTERNS: list[tuple[str, str]] = [
    # (fnmatch pattern, provider_family)
    # 具体模式在前，通配在后
    ("deepseek-*",         "deepseek"),
    ("gpt-*",              "openai"),
    ("o1-*",               "openai"),
    ("o3-*",               "openai"),
    ("o4-*",               "openai"),
    ("gemini-*",           "gemini"),
    ("claude-*",           "anthropic"),
    ("qwen-*",             "qwen"),
    ("doubao-*",           "doubao"),
    ("glm-*",              "zhipu"),
]

_PROVIDER_CAPABILITIES: dict[str, ProviderCaps] = {
    "deepseek": ProviderCaps(
        supported_efforts={"low", "medium", "high", "max"},
        disable_mode="thinking_object",    # thinking.type="disabled"
        effort_mapping={"low": "high", "medium": "high", "xhigh": "max"},
    ),
    "openai": ProviderCaps(
        supported_efforts={"none", "minimal", "low", "medium", "high"},
        disable_mode="effort_none",        # reasoning_effort="none"
        effort_mapping={},
    ),
    "gemini": ProviderCaps(
        supported_efforts={"none", "minimal", "low", "medium", "high"},
        disable_mode="effort_none",
        effort_mapping={},
    ),
    # 可扩展...
}
```

**用户可自定义**：

```python
from llm_compat.providers import register_provider, ProviderCaps

# 注册自定义 provider（如代理服务重命名了模型）
register_provider(
    pattern="my-proxy-ds-*",
    family="deepseek",
)

# 或注册全新 provider
register_provider(
    pattern="yi-*",
    family="yi",
    caps=ProviderCaps(
        supported_efforts={"low", "medium", "high"},
        disable_mode="effort_none",
        effort_mapping={},
    ),
)
```

### 3. JSON 响应清洗（json_utils.py）

```python
from llm_compat.json_utils import parse_json, parse_json_model

# 清洗 + 解析
data = parse_json('```json\n{"tags": ["tech"]}\n```')
# → {"tags": ["tech"]}

# 清洗 + Pydantic 校验 + bare list 自动包装
result = parse_json_model(raw_content, TagResult)
# → TagResult(tags=["tech"])
```

处理的兼容性问题：
- Markdown code fence 剥离（` ```json ... ``` `）
- Bare list 自动包装到 schema 的 list 字段
- 首尾空白清理

### 4. 重试（retry.py）

```python
# 内部使用，不需要用户直接调用
# 与当前 Memos 项目的实现一致：
# - 指数退避 + jitter
# - 429 Retry-After 响应
# - 可重试状态码：429, 500, 502, 503, 504
# - 可重试异常：TimeoutException, NetworkError
```

### 5. 可观测性（内置于 client.py）

每次请求自动记录：

```
[a1b2c3d4] LLM request  | model=deepseek-v4-flash (deepseek) | thinking=high | messages=3
[a1b2c3d4] LLM response | latency=1823ms | tokens=156/892/1048
[e5f6g7h8] LLM error    | model=gpt-4o | attempt=2/4 | 429 Rate Limited | retry_after=2.0s
```

**日志框架无关**：使用标准 `logging` 模块，用户自行配置 handler（loguru、structlog 等通过 bridge 接入）。

## reasoning_effort 统一值域

| 配置值 | 语义 | DeepSeek | OpenAI | Gemini | 未知 |
|--------|------|----------|--------|--------|------|
| `None` | 不设置，用 provider 默认 | 不发 | 不发 | 不发 | 不发 |
| `"disabled"` | 显式关闭思考 | `thinking.type=disabled` | `effort=none` | `effort=none` | 丢弃+warn |
| `"low"` | 低 | 映射→`high` | 透传 | 透传 | 透传 |
| `"medium"` | 中 | 映射→`high` | 透传 | 透传 | 透传 |
| `"high"` | 高 | 透传 | 透传 | 透传 | 透传 |
| `"max"` | 最高（DeepSeek 独有） | 透传 | clamp→`high`+warn | clamp→`high`+warn | 透传 |
| 其他字符串 | 未知值 | 透传 | 透传 | 透传 | 透传 |

`"none"` 作为 legacy 别名，自动归一到 `"disabled"` + deprecation warn。

## 各项目迁移示例

### Before（Memos 项目，直接 httpx）

```python
# core/clients/llm_client.py — 538 行
class LLMClient:
    def _build_payload(self, cfg, messages, ...):
        if cfg.reasoning_effort is not None:
            payload["reasoning_effort"] = cfg.reasoning_effort  # 无法关闭思考
        ...
```

### After（使用 llm-compat）

```python
# core/clients/llm_client.py — ~200 行
from llm_compat import LLMClient as BaseLLMClient

class LLMClient:
    """项目级封装：加载配置 + prompt 模板 + task_overrides 解析"""

    def __init__(self, config: LLMConfig):
        self._base = BaseLLMClient(
            base_url=config.text.base_url,
            api_key=config.text.api_key,
        )
        self._config = config

    async def chat(self, messages, *, task_name=None, use_image=False):
        cfg = self._resolve_config(task_name, use_image=use_image)
        return await self._base.chat(
            model=cfg.model,
            messages=messages,
            reasoning_effort=cfg.reasoning_effort,  # "disabled" 现在能正确翻译
        )
```

项目级 LLMClient 只保留**配置解析 + task_overrides**逻辑，HTTP/重试/翻译全部下沉到 llm-compat。

## 安装方式

```bash
# 从 GitHub 安装（私有包，不发 PyPI）
uv add git+https://github.com/zj1123581321/llm-compat.git

# 或锁定版本
uv add git+https://github.com/zj1123581321/llm-compat.git@v0.1.0

# 带 Pydantic 支持
uv add "llm-compat[pydantic] @ git+https://github.com/zj1123581321/llm-compat.git"
```

## 版本策略

- 厂商 API 改版（如新增 provider / 值域变化）→ patch 版本
- 新增 API 方法 → minor 版本
- 破坏性变更 → major 版本（尽量不发生）

## 下一步

1. 确认设计方向
2. 创建 `llm-compat` 仓库，实现核心模块（~300 行）
3. Memos 项目作为第一个消费者迁移验证
4. 逐步迁移其他项目
