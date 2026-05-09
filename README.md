# llm-compat

轻量 Python 包，抹平 OpenAI 兼容 API 的多 provider 差异。专为 New API 代理 + 多模型切换场景设计。

## 解决什么问题

10+ 个 Python 项目通过 New API（OpenAI 兼容代理）接入 DeepSeek、GPT、Gemini 等模型，切换模型时：

- `thinking` / `reasoning_effort` 参数各家写法不同，静默忽略不报错
- JSON 输出格式不一致（code fence、bare list）
- 重试、日志、超时每个项目都在重复写
- 国内模型因内容审查拒绝回答，需要手动切换到海外模型

llm-compat 统一处理这些差异，业务代码只需改配置不改代码。

## 安装

```bash
# 从 GitHub 安装
uv add git+https://github.com/zj1123581321/llm-compat.git

# 锁定版本
uv add git+https://github.com/zj1123581321/llm-compat.git@v0.1.0

# 启用敏感词前置检测（可选）
uv add "git+https://github.com/zj1123581321/llm-compat.git[sensitive]"
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

支持 10 个 provider 族：`deepseek` / `gemini_25` / `gemini_3` / `gemini` / `openai_gpt5` / `openai_gpt4` / `openai_o` / `doubao_seed` / `doubao` / `openai`

### 自定义 Provider

```python
from llm_compat import register_provider

# 代理服务重命名了模型
register_provider("my-proxy-ds-*", "deepseek")
```

## 内容审查降级（Content Fallback）

国内模型（DeepSeek、Qwen 等）因内容审查拒绝回答时，自动降级到海外模型：

```python
async with LLMClient(
    base_url="https://your-newapi.com/v1",
    api_key="sk-xxx",
    content_fallbacks={
        "deepseek-*": ["gpt-4.1-mini", "gemini-2.5-flash"],
        "qwen-*": ["gpt-4.1-mini"],
    },
) as client:
    result = await client.chat("deepseek-v4", messages)
    # 如果 deepseek-v4 被拒绝，自动尝试 gpt-4.1-mini，再不行尝试 gemini-2.5-flash
    print(result.model)          # 实际使用的模型
    print(result.fallback_from)  # 原始模型（未降级时为 None）
```

### 检测机制（三层）

1. **结构化信号**（最可靠）：`finish_reason=content_filter`、空 `choices`、`refusal` 字段
2. **HTTP 错误码**：400/403 + response body 包含审查关键词（`content_policy`、`blocked` 等）
3. **响应文本关键词**（兜底）：内置中英文拒绝关键词列表

### 模态感知

`chat_image` 请求降级时自动跳过不支持图片的模型：

```python
# deepseek 不支持 vision，fallback 链中只会尝试支持 vision 的模型
result = await client.chat_image("deepseek-v4", "描述这张图", image_data=img, media_type="image/png")
```

### 自定义检测

```python
from llm_compat import RefusalContext

def my_detector(ctx: RefusalContext) -> bool:
    # ctx.content, ctx.model, ctx.provider, ctx.finish_reason 可用
    return "自定义拒绝标识" in ctx.content

client = LLMClient(
    ...,
    content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
    refusal_detector=my_detector,
    refusal_keywords=["额外关键词"],  # 追加到内置列表
)
```

### 前置敏感词检测（可选）

发送前检测输入内容，直接跳过主模型，省一次 API 调用：

```python
from llm_compat.sensitive import SensitiveDetector

detector = SensitiveDetector(words=["敏感词1", "敏感词2"])
client = LLMClient(
    ...,
    content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
    sensitive_detector=detector,
)
# 输入包含敏感词时，直接用 fallback 模型，不浪费主模型的 API 调用
```

需要安装 `llm-compat[sensitive]` 以启用 Aho-Corasick 高性能匹配（不安装也可用，降级到纯 Python 扫描）。

### 配置校验

```python
from llm_compat import validate_fallback_config

warnings = validate_fallback_config({
    "gpt-4.1-*": ["deepseek-chat"],  # deepseek 不支持 vision
})
# ['Pattern gpt-4.1-* supports vision but no fallback model supports vision; ...']
```

### 统计

```python
stats = client.stats
print(f"降级次数: {stats.fallback_count}")
print(f"前置跳过: {stats.prescan_skips}")
print(f"各模型拒绝: {stats._refusal_counts}")
```

### 已知限制

- `chat_stream()` 不支持响应端 fallback（前置敏感词检测可部分覆盖流式场景）
- fallback 配置为 init 级别，运行时不可动态修改（需创建多个 client 实例）

## 敏感词积累（Collector）

跨项目自动收集拒绝事件，人工审核后提取敏感词，闭环回 pre-scan。

### 部署 Collector 服务

```bash
docker network create llm-net
cd collector
# 设置 API Key（可选，不设则不鉴权）
echo "COLLECTOR_API_KEY=your-secret" > .env
docker compose up -d
```

### 集成到项目

```python
async with LLMClient(
    base_url="https://your-newapi.com/v1",
    api_key="sk-xxx",
    content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
    # Collector 集成（可选，不配则不上报）
    collector_url="http://llm-compat-collector:8000",
    collector_project="my-project",       # 来源标识，区分哪个项目触发的拒绝
    collector_api_key="your-secret",      # 与 COLLECTOR_API_KEY 一致
) as client:
    result = await client.chat("deepseek-v4", messages)
    # fallback 触发时自动上报拒绝事件到 collector
```

各项目的 docker-compose 需加入同一网络：

```yaml
networks:
  llm-net:
    external: true
```

### 日常使用

```bash
# 查看拒绝统计
curl http://localhost:8234/stats | jq

# 查看最近拒绝事件（含输入摘要）
curl http://localhost:8234/stats | jq '.recent_refusals'

# 审核后加词（需要 API Key）
curl -X POST http://localhost:8234/words \
  -H 'Authorization: Bearer your-secret' \
  -H 'Content-Type: application/json' \
  -d '{"word": "敏感词"}'

# 查看当前词表
curl http://localhost:8234/words | jq
```

### Collector API

| 端点 | 方法 | 鉴权 | 说明 |
|------|------|------|------|
| `/refusals` | POST | 需要 | 上报拒绝事件 |
| `/words` | GET | 不需要 | 获取当前词表 + hash |
| `/words` | POST | 需要 | 添加敏感词 |
| `/words/hash` | GET | 不需要 | 词表变更检测 |
| `/words/{word}` | DELETE | 需要 | 删除误报词 |
| `/stats` | GET | 不需要 | 拒绝统计 |

## 启动校验

```python
from llm_compat import validate_config

warnings = validate_config("gpt-4.1-mini", "high")
# ['Provider openai_gpt4 does not support reasoning_effort; value high will be dropped']
```

## 错误处理

```python
from llm_compat import FatalError, TimeoutError, JSONParseError, ContentPolicyError

try:
    result = await client.chat_json("gpt-4o", messages, schema=MyModel)
except ContentPolicyError as e:
    print(e.attempted_models)  # 所有尝试过的模型
    print(e.raw_content)       # 最后一个模型的拒绝内容
    print(e.original_model)    # 原始请求的模型
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
src/llm_compat/
├── _base.py          — 共享基类 + generator fallback 编排
├── _collector.py     — Collector 服务客户端（上报/拉取/降级缓存）
├── client.py         — async client（I/O 层）
├── sync.py           — sync client（I/O 层）
├── providers.py      — 10 族检测 + thinking 翻译 + supports_vision
├── retry.py          — 智能重试 + 错误分类
├── refusal.py        — 3 层拒绝检测（结构化信号/HTTP/关键词）
├── fallback.py       — fallback 链解析 + 模态过滤
├── sensitive.py      — 前置敏感词检测（可选依赖）
├── _types.py         — ChatResult, TokenUsage, LLMStats
├── _compat.py        — validate_config, validate_fallback_config
├── errors.py         — 错误层级 + ContentPolicyError
├── json_utils.py     — JSON 清洗 + Pydantic 校验
└── __init__.py       — 公开 API

collector/            — Sidecar 服务（FastAPI + SQLite），17 tests
tests/                249 tests
```

## 依赖

- `httpx>=0.27` — HTTP 客户端
- `pydantic>=2.0` — JSON 校验

可选依赖：
- `pyahocorasick>=2.0` — 高性能敏感词检测（`pip install llm-compat[sensitive]`）

## License

MIT
