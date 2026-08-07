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
    "httpx[socks]>=0.27",
    "pydantic>=2.0",
]

[project.optional-dependencies]
sensitive = ["pyahocorasick>=2.0"]  # 可选的敏感词检测加速
```

运行时依赖是 `httpx[socks]` 与 `pydantic`；敏感词检测的 `pyahocorasick` 为可选依赖。

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
# 返回: ChatResult（`str(result)` 可取 content）

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
# 返回: ChatResult，`result.parsed` 为 TagResult 实例

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
- `chat` 与 `chat_json` 都返回 `ChatResult`；用 `str(result)` 取文本，`chat_json` 的
  解析对象从 `result.parsed` 读取

### 2. Provider 翻译（providers.py）

```python
from llm_compat.providers import build_request_payload, detect_provider

detection = detect_provider("deepseek-chat")
assert detection.family == "deepseek" and detection.matched
# 按模型匹配的 family 翻译统一的 reasoning_effort：
build_request_payload("deepseek-chat", "disabled", {"model": "deepseek-chat"})
# → {"model": "deepseek-chat", "thinking": {"type": "disabled"}}
```

运行时能力记录是 `dict[str, Any]`，当前 10 个 family/18 个有序 pattern 以 `caps.json`
及其 schema/enums 为准；`ProviderDetection` 用 `.family` 和 `.matched` 区分兜底与命中。

自定义 family 的完整 `register_provider(..., caps={...})` 用法见接入指南；字段必须符合
`caps.json` 的 schema/enums。只传 `pattern` 与已有 family 时，是复用该 family 的能力记录。

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

family 的 `disable_mode`、efforts、`json_mode`、`supports_vision` 以 `caps.json.families` 及 schema/enums 为准；不要再维护旧示例。

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
uv add git+https://github.com/zlxlabs/llm-compat.git

# 或锁定版本
uv add git+https://github.com/zlxlabs/llm-compat.git@v0.9.0
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
