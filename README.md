# llm-compat

轻量 Python 包，抹平 OpenAI 兼容 API 的多 provider 差异。专为 New API 代理 + 多模型切换场景设计。

## 解决什么问题

10+ 个 Python 项目通过 New API（OpenAI 兼容代理）接入 DeepSeek、GPT、Gemini 等模型，切换模型时：

- `thinking` / `reasoning_effort` 参数各家写法不同，静默忽略不报错
- JSON 输出格式不一致（code fence、bare list）
- 重试、日志、超时每个项目都在重复写

llm-compat 统一处理这些差异，业务代码只需改配置不改代码。

## 安装

```bash
# 从 GitHub 安装
uv add git+https://github.com/zj1123581321/llm-compat.git

# 锁定版本
uv add git+https://github.com/zj1123581321/llm-compat.git@v0.1.0
```

## 快速开始

### 异步（推荐）

```python
from llm_compat import LLMClient

async with LLMClient(
    base_url="https://your-newapi.com/v1",
    api_key="sk-xxx",
) as client:
    # 基础对话
    result = await client.chat(
        "deepseek-v4-flash",
        [{"role": "user", "content": "hello"}],
        reasoning_effort="high",
    )
    print(result)              # 直接当 str 用
    print(result.usage)        # TokenUsage(prompt=8, completion=75, total=83)
    print(result.latency_ms)   # 1519
    print(result.request_id)   # a1b2c3d4
```

### 同步

```python
from llm_compat import SyncLLMClient

with SyncLLMClient(base_url="...", api_key="...") as client:
    result = client.chat("gpt-4.1-mini", messages)
```

### 结构化 JSON 输出

```python
from pydantic import BaseModel

class TagResult(BaseModel):
    tags: list[str]

result = await client.chat_json(
    "gpt-4.1-mini",
    [{"role": "user", "content": "给 Python 打 3 个标签"}],
    schema=TagResult,
)
print(result.parsed)  # TagResult(tags=['编程语言', 'Python', '开发'])
```

自动处理 code fence 剥离、bare list 包装、Pydantic 校验。

### 流式输出

```python
async for chunk in client.chat_stream("deepseek-v4-flash", messages):
    print(chunk, end="")
```

### 多模态图片

```python
result = await client.chat_image(
    "gpt-4o", "描述这张图",
    image_data=raw_bytes, media_type="image/png",
)
```

## Provider 翻译

同一个 `reasoning_effort` 参数，自动按模型翻译：

| 配置值 | DeepSeek V4 | OpenAI GPT-5 | Gemini 2.5 | GPT-4.x |
|--------|------------|--------------|------------|---------|
| `None` | 不发 | 不发 | 不发 | 不发 |
| `"disabled"` | `thinking.type=disabled` | `effort=minimal` | `effort=none` | 忽略(不思考) |
| `"high"` | 透传 | 透传 | 透传 | drop+warn |
| `"max"` | 透传 | clamp→`high` | clamp→`high` | drop+warn |

支持 8 个 provider 族：`deepseek` / `gemini_25` / `gemini_3` / `gemini` / `openai_gpt5` / `openai_gpt4` / `openai_o` / `openai`

### 自定义 Provider

```python
from llm_compat import register_provider

# 代理服务重命名了模型
register_provider("my-proxy-ds-*", "deepseek")
```

## 启动校验

```python
from llm_compat import validate_config

warnings = validate_config("gpt-4.1-mini", "high")
# ['Provider openai_gpt4 does not support reasoning_effort; value high will be dropped']
```

## 错误处理

```python
from llm_compat import FatalError, TimeoutError, JSONParseError

try:
    result = await client.chat_json("gpt-4o", messages, schema=MyModel)
except JSONParseError as e:
    print(e.raw_content)   # 模型返回的原始内容
    print(e.model)         # gpt-4o
    print(e.request_id)    # 追踪 ID
except TimeoutError:
    pass  # 不会重试（同样的输入大概率同样超时）
except FatalError:
    pass  # 401/403/404，不会重试
```

## 日志

每次请求自动记录（标准 `logging`，消费者自行配置 handler）：

```
[a1b2c3d4] LLM request  | model=deepseek-v4-flash (deepseek) | thinking=high | messages=3
[a1b2c3d4] LLM response | latency=1823ms | tokens=156/892/1048
```

## 统计

```python
stats = client.stats
print(f"调用: {stats.total_calls}, 成功率: {stats.success_rate:.0%}, tokens: {stats.total_tokens}")
```

## 包结构

```
src/llm_compat/       1139 行
├── providers.py      264  — 8 族检测 + thinking 翻译
├── client.py         248  — async client
├── sync.py           190  — sync client (真 httpx.Client)
├── retry.py          140  — 智能重试 + 错误分类
├── _types.py          67  — ChatResult, TokenUsage, LLMStats
├── _compat.py         67  — validate_config, normalize_effort
├── errors.py          66  — 错误层级
├── json_utils.py      53  — JSON 清洗 + Pydantic 校验
└── __init__.py        44  — 公开 API

tests/                1144 行, 141 tests
```

## 依赖

- `httpx>=0.27` — HTTP 客户端
- `pydantic>=2.0` — JSON 校验

零其他依赖。

## License

MIT
