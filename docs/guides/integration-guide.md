# llm-compat 接入指南

## 快速开始

```bash
uv add git+https://github.com/zlxlabs/llm-compat.git
```

v0.9.0 将 provider 检测结果结构化，并新增可选的未知模型严格模式；默认仍宽松兼容。
```python
from llm_compat import LLMClient

async with LLMClient(
    base_url="https://your-newapi.com/v1",
    api_key="sk-xxx",
) as client:
    result = await client.chat(
        "deepseek-v4-flash",
        [{"role": "user", "content": "hello"}],
        reasoning_effort="high",
    )
    print(result)              # 直接当 str 用
    print(result.usage)        # TokenUsage(prompt=8, completion=75, total=83, reasoning=0)
    print(result.finish_reason)  # 上游 finish_reason；缺失时为 None
    print(result.latency_ms)   # 1519
```

---

## 核心 API

### chat() — 文本对话

```python
result = await client.chat(
    "deepseek-v4-flash",
    [{"role": "user", "content": "hello"}],
    reasoning_effort="high",     # 可选，自动按 provider 翻译
)
```

返回 `ChatResult`，可直接当 `str` 使用。内置重试（指数退避 + jitter）、请求日志、token 统计。

### chat_json() — 结构化 JSON 输出

让 LLM 返回结构化 JSON。自动处理 provider 差异，调用方无需关心底层用的是 json_schema 还是 json_object 模式。

#### Pydantic Schema（推荐）

```python
from pydantic import BaseModel

class TagResult(BaseModel):
    tags: list[str]

result = await client.chat_json(
    "gpt-5-mini",
    [{"role": "user", "content": "给 Python 打 3 个标签"}],
    schema=TagResult,
)
print(result.parsed)       # TagResult(tags=['编程语言', 'Python', '开发'])
print(type(result.parsed)) # <class 'TagResult'>
```

#### 原始 JSON Schema dict

不用 Pydantic 时可以直接传 JSON Schema：

```python
result = await client.chat_json(
    "gpt-5-mini",
    messages,
    json_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
)
print(result.parsed)  # dict, 如 {"name": "Python"}
```

同时传 `schema` + `json_schema` 时，`json_schema` 优先用于 API 的 `response_format`，`schema` 用于反序列化。

只传 `json_schema` 时，`json_schema` 仅用于 API 请求侧的 `response_format`，返回的 `parsed` 字典不会在本地做结构校验。
如果需要校验返回值，请传入 `schema=<PydanticModel>`；库会用该模型反序列化并校验结果。只传 `json_schema` 时，库会打一条
warning 明确提示这一限制。

#### 不传 Schema

只保证返回合法 JSON，不约束结构：

```python
result = await client.chat_json("gpt-5-mini", messages)
print(result.parsed)  # dict
```

#### 自动模式选择

`chat_json()` 根据 provider 能力自动选择最优模式：

| Provider 族 | 模式 | 代表模型 | 说明 |
|---|---|---|---|
| `openai_gpt5` / `openai_o` / `gemini_25` / `gemini_3` / `doubao` / `doubao_seed` | json_schema | gpt-5-mini, o4-mini, gemini-2.5-flash | API 层面强制 Schema 约束，成功率最高 |
| `openai_gpt4` / `deepseek` / `gemini` / `mimo` | json_object | gpt-4o, deepseek-v4-flash, mimo-v2.5-pro | API 保证合法 JSON，schema 通过 prompt 注入引导结构 |

- json_schema 模式被 API 拒绝（400）时自动降级到 json_object + warning 日志
- json_object 模式下自动将 schema 注入到最后一条 user message 中引导输出格式
- 多模态消息（图片+文本）的 schema 注入也能正确处理

#### Self-Correction

解析失败时自动将错误反馈给模型重试：

```python
result = await client.chat_json(
    "deepseek-v4-flash",
    messages,
    schema=TagResult,
    self_correction=True,   # 开启
    max_retries=2,          # 最多重试 2 次（默认）
)
```

流程：模型返回错误 JSON → 追加错误信息到 messages → 重新请求 → 直到解析通过或重试耗尽（抛 `JSONParseError`）。

### chat_stream() — 流式输出

```python
async for chunk in client.chat_stream("deepseek-v4-flash", messages):
    print(chunk, end="")
```

注意：流式模式不支持结构化 JSON 输出和响应端 content fallback。前置敏感词检测可部分覆盖流式场景。

### chat_image() / chat_images() — 多模态

```python
# 单张图片
result = await client.chat_image(
    "gpt-4o", "描述这张图",
    image_data=raw_bytes, media_type="image/png",
)

# 多张图片
result = await client.chat_images(
    "gpt-4o", "比较这两张图的区别",
    images=[(bytes1, "image/png"), (bytes2, "image/jpeg")],
)
```

自动处理 base64 编码。配合 `chat_json()` 可从图片提取结构化数据：

```python
# 多模态 + JSON：图片分析返回结构化数据
result = await client.chat_json(
    "gpt-4o",
    [{"role": "user", "content": [
        {"type": "text", "text": "提取图中的产品信息"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
    ]}],
    schema=ProductInfo,
)
```

### SyncLLMClient — 同步客户端

API 与 `LLMClient` 完全一致，用于非 async 场景：

```python
from llm_compat import SyncLLMClient

with SyncLLMClient(base_url="...", api_key="...") as client:
    result = client.chat("gpt-4.1-mini", messages)
    result = client.chat_json("deepseek-v4", messages, schema=TagResult)
```

---

## 内容审查降级

国内模型（DeepSeek 等）因内容审查拒绝回答时，自动降级到海外模型。**`chat()` 和 `chat_json()` 均支持。**

### 配置

```python
async with LLMClient(
    base_url="https://your-newapi.com/v1",
    api_key="sk-xxx",
    content_fallbacks={
        "deepseek-v4-pro": ["gemini-3-flash-preview", "gemini-2.5-flash"],
        "deepseek-v4-flash": ["gemini-3.1-flash-lite-preview"],
    },
) as client:
    # chat() 和 chat_json() 共享同一套 fallback 逻辑
    result = await client.chat("deepseek-v4-pro", messages)
    result = await client.chat_json("deepseek-v4-pro", messages, schema=MyModel)

    print(result.model)          # 实际使用的模型
    print(result.fallback_from)  # 原始模型（未降级时为 None）
```

fallback 切换模型时，`chat_json()` 自动为新模型重新选择 json_mode。例如 DeepSeek（json_object）被拒后 fallback 到 GPT-5（json_schema），会自动使用更高级的 json_schema 模式。

### 拒绝检测（证据分级，自动）

1. **声明层**（最可靠）：provider 的 `finish_reason=content_filter`/`content_policy`/`safety` 或非空
   `message.refusal`。这类拒绝不可被文本 detector 否决，也不会在链耗尽时被救援。
2. **推断层**：默认中英文拒绝言语行为正则，且必须同时满足全文不超过 300 字、命中在前 120
   字；普通题材词（例如“违反”“无法提供”“violates”）不会单独触发。
3. **HTTP 错误**：400/403/451/500 且响应体被分类为内容审查错误；它不提供可救援的正文。

每次拒绝判定都产生 `RefusalEvidence`，包含 `layer`、`signal`、命中片段、位置、正文长度和
`finish_reason`，同时写入 WARNING 日志和 Collector（如果已配置）。

### 模态感知

图片请求降级时自动跳过不支持 vision 的模型：

```python
result = await client.chat_image("deepseek-v4", "描述这张图", image_data=img, media_type="image/png")
# deepseek 不支持 vision，fallback 链中只尝试支持 vision 的模型
```

### 前置敏感词检测（可选）

已知的敏感词可在发送前检测，直接跳过主模型，省一次 API 调用：

```bash
uv add "git+https://github.com/zlxlabs/llm-compat.git[sensitive]"
```

#### 方式一：从 URL 加载词库（推荐）

```python
client = LLMClient(
    ...,
    content_fallbacks={...},
    sensitive_words_url="http://llm-compat-collector:8000/words.txt",
)
```

- 支持 `str`（单 URL）或 `list[str]`（多 URL）
- URL 返回纯文本格式：每行一个词，`#` 开头为注释行，空行自动忽略
- 同进程多实例共享缓存，后台每 5 分钟刷新
- URL 不可达时保留旧缓存，不影响业务

#### 方式二：手动指定词库

```python
from llm_compat.sensitive import SensitiveDetector

detector = SensitiveDetector(words=["敏感词1", "敏感词2"])
client = LLMClient(
    ...,
    content_fallbacks={...},
    sensitive_detector=detector,
)
```

#### 方式三：URL + 手动词库合并

```python
client = LLMClient(
    ...,
    content_fallbacks={...},
    sensitive_words_url="http://llm-compat-collector:8000/words.txt",
    sensitive_detector=SensitiveDetector(words=["本地额外词"]),
)
```

### 自定义拒绝检测

```python
from llm_compat import RefusalContext

def my_detector(ctx: RefusalContext) -> bool | None:
    return "自定义拒绝标识" in ctx.content

client = LLMClient(
    ...,
    content_fallbacks={...},
    refusal_detector=my_detector,
    refusal_keywords=["额外关键词"],      # 默认追加到内置句式
)
```

detector 是三态函数：返回 `True` 表示拒绝，返回 `False` 表示确认非拒绝并短路内置文本判定，
返回 `None` 交给默认逻辑。detector 抛异常时记录 WARNING 并继续默认逻辑。声明层信号始终优先。

需要完全接管文本词表时使用 `refusal_keywords_mode="replace"`；不传关键词时文本层不判拒绝，
但 provider 声明层仍然有效。手动词和 URL 词按子串匹配，仍受相同的长度/位置门槛约束。

### 拒绝检测的边界与调优

内置文本层是启发式，不是语义判定器。模型先道歉或限定，再继续作答时，内置句式表始终会有
残余的假阳性和漏判风险：词法形态可以不断变化，但正则无法可靠判断语用意图。库的取舍是偏向
漏判，因为漏判只会把一句拒绝语交给下游，调用方仍可恢复；误判则可能丢弃正确结果，无法恢复。

已知的文本层残余假阳性包括：

- **因果补充**：拒绝动词后的因果补充（例如“因为……”或英文 “as …”）不是转折排除信号，
  后文若继续作答，仍可能被整段判为拒绝。
- **跨句转折后继续作答**：转折排除只在拒绝动词后约 30 字、且不跨句末标点时生效，
  因此“我无法提供精确数字。不过，根据现有数据估计约为 40%。”和英文对应句式仍可能被判为拒绝。
  跨句的“不过”或 “However” 既可能引出任务答案，也可能引出替代建议（后者仍是拒绝），
  无法靠位置区分；库选择保守判为拒绝，误判代价由 `on_all_refused="return_best"` 兜住。

文本层误判的代价有上限。`on_all_refused` 默认是 `"return_best"`，因此文本层把正常回答误判为
拒绝时不会直接让调用方拿到失败：最坏情况是多试几次 fallback，最后仍返回正文，并设置
`refusal_suspected=True`。调用方应检查这个字段和 `refusal_evidence`，再决定是否需要业务降级。
provider 的声明层信号仍然优先，`finish_reason=content_filter` 或非空 `message.refusal` 不会被
文本层配置关闭。

需要关闭或接管文本层时，使用现有的三个逃生门。下面的示例都保留声明层判定：

1. 完全关闭文本层，只保留 provider 声明层：

   ```python
   from llm_compat import LLMClient

   client = LLMClient(
       base_url="https://api.example.com/v1",
       api_key="sk-example",
       refusal_max_content_length=0,
   )
   ```

2. 完全接管文本词表，内置句式不再参与文本判定：

   ```python
   from llm_compat import LLMClient

   client = LLMClient(
       base_url="https://api.example.com/v1",
       api_key="sk-example",
       refusal_keywords_mode="replace",
       refusal_keywords=["供应商明确拒绝"],
   )
   ```

3. 对单次响应由调用方确认不是拒绝，短路内置文本判定：

   ```python
   from llm_compat import LLMClient, RefusalContext

   def detector(ctx: RefusalContext) -> bool | None:
       if ctx.content == "抱歉，我无法提供相关信息":
           return False
       return None

   client = LLMClient(
       base_url="https://api.example.com/v1",
       api_key="sk-example",
       refusal_detector=detector,
   )
   ```

后续新发现的假阳性或漏判个案，优先通过上述配置由调用方处理，或提 issue 记入 backlog；默认
正则表不再按个案逐条修改。句首冒号、Markdown 列表或加粗等已知漏判属于“偏向漏判”的取舍，
不应为覆盖单个样本而放宽默认句式边界。

### 动态关键词加载

从 URL 动态加载拒绝关键词，扩大检测覆盖面：

```python
client = LLMClient(
    ...,
    content_fallbacks={...},
    refusal_keywords_url=[
        "http://llm-compat-collector:8000/words.txt",
        "https://cdn.internal/shared-keywords.txt",
    ],
)
```

- 支持 `str`（单 URL）或 `list[str]`（多 URL）
- URL 返回纯文本格式：每行一个词，`#` 开头为注释行，空行自动忽略
- 同进程多实例共享缓存，后台每 5 分钟刷新
- URL 不可达时保留旧缓存

### 所有模型都拒绝时

默认 `on_all_refused="return_best"`。救援只从推断层候选中挑选正文最长者；声明层候选自身一律不救。
链上同时存在声明层与推断层候选时，仍会救援推断层候选；若没有非空的推断层候选，抛
`ContentPolicyError`。被救援的候选设置 `refusal_suspected=True` 与 `refusal_evidence`；
`chat_json()` 会重新执行 JSON 清洗和 schema 校验，校验失败仍抛 `ContentPolicyError`。

```python
from llm_compat import ContentPolicyError

try:
    result = await client.chat("deepseek-v4-pro", messages)
    if result.refusal_suspected:
        logger.warning("best candidate was inferred as a refusal: %s", result.refusal_evidence)
except ContentPolicyError as e:
    print(e.attempted_models)  # ['deepseek-v4-pro', 'gemini-3-flash-preview', ...]
    print(e.raw_content)       # 最后一个模型的拒绝内容
    print(e.original_model)    # 'deepseek-v4-pro'
    print(e.attempt_layers)    # {model: layer}
```

如果业务必须在推断层也失败，可显式传 `on_all_refused="raise"`。原始返回结果仍在
`ContentPolicyError.raw_content` 中，判定详情在 `e.evidence`。

### 已知限制

- `chat_stream()` 不支持响应端 fallback（前置敏感词检测可部分覆盖流式场景）
- fallback 配置为 init 级别，运行时不可动态修改

---

## 生产环境配置

### 重试与超时

```python
client = LLMClient(
    ...,
    max_retries=3,         # 网络错误/可重试错误的重试次数（默认 3）
    base_delay=1.0,        # 首次重试延迟（默认 1s）
    max_delay=60.0,        # 最大退避延迟（默认 60s）
    total_timeout=300.0,   # 单次请求总超时（默认 300s）
)
```

错误分类：
- **RetryableError**：网络错误、502/503/429 → 自动重试
- **FatalError**：401/403/404 → 不重试
- **TimeoutError**：超时 → 不重试（同样输入大概率同样超时）

### 并发控制

```python
client = LLMClient(..., max_concurrency=30)
```

内部 `asyncio.Semaphore`，不设则不限制。

### 生命周期 Hook

```python
client = LLMClient(
    ...,
    on_success=lambda model, latency_ms: print(f"{model} ok in {latency_ms}ms"),
    on_error=lambda model, error: print(f"{model} failed: {error}"),
    pre_request=lambda model: breaker.can_execute(),  # 返回 False 抛 SkipRequestError
)
```

| Hook | 类型 | 异常处理 |
|------|------|---------|
| `on_success(model, latency_ms)` | 观察性 | 异常吞掉，记 warning |
| `on_error(model, error)` | 观察性 | 异常吞掉，记 warning |
| `pre_request(model) → bool` | 控制性 | 返回 False 抛 `SkipRequestError` |

### 错误处理

```python
from llm_compat import FatalError, TimeoutError, JSONParseError, ContentPolicyError, SkipRequestError

try:
    result = await client.chat_json("gpt-4o", messages, schema=MyModel)
except ContentPolicyError as e:
    print(e.attempted_models)  # 所有尝试过的模型
    print(e.raw_content)       # 最后一个模型的拒绝内容
except JSONParseError as e:
    print(e.raw_content)       # 模型返回的原始内容
    print(e.model)
    print(e.request_id)
except SkipRequestError:
    pass  # pre_request hook 返回 False
except TimeoutError:
    pass  # 不重试
except FatalError:
    pass  # 401/403/404，不重试
```

### 日志

每次请求自动记录（标准 `logging`）：

```
[a1b2c3d4] LLM request  | model=deepseek-v4-flash (deepseek) | thinking=high | messages=3
[a1b2c3d4] LLM response | latency=1823ms | tokens=156/892/1048
```

### 统计

```python
stats = client.stats

# 基础
stats.total_calls          # 总调用次数
stats.success_rate         # 成功率
stats.total_tokens         # 总 token 数
stats.total_latency_ms     # 总延迟

# JSON 输出
stats.json_schema_calls    # json_schema 模式调用次数
stats.json_object_calls    # json_object 模式调用次数
stats.json_parse_failures  # JSON 解析失败次数
stats.json_self_correction_success  # self-correction 成功次数

# Content fallback
stats.fallback_count       # 降级次数
stats.prescan_skips        # 前置跳过次数
stats._refusal_counts      # 各模型拒绝次数 dict
```

一次逻辑调用（`chat` / `chat_json` / `chat_image` / `chat_images`，含 sync 版）在 `stats` 上恰好留下一条记录：成功记 `success_count`，失败记 `error_count`，且恒有 `total_calls == success_count + error_count`。JSON 解析或 schema 校验失败只记 error，不再先记一条 success。`fallback_count` / `json_parse_failures` 是过程计数，不占用这条「一次调用一条」口径。

`chat_stream()` 不走编排器，当前既不记账也不上报 collector（既有边界，不是漏记）。

### Collector 上报覆盖面

配了 `collector_url` 时，`chat()` / `chat_json()` / `chat_image()` / `chat_images()` 在判定到拒绝后会上报 sidecar：

- 覆盖成功路径里被判拒的中间模型（随后 fallback 或救援成功也上报），以及整链 `ContentPolicyError`、被分类为 `http_error` 的 HTTP 拒绝。
- 上报发生在异常抛出给调用方**之前**；JSON 解析失败、普通 4xx/5xx/网络错误不上报。
- 没配 collector 时失败路径不额外报错，`ContentPolicyError.evidence` / `attempt_layers` 仍保留。
- `SyncLLMClient` 不报送（`CollectorClient.report_refusal` 是异步接口，这是既有边界）。

### 启动校验

```python
from llm_compat import validate_config, validate_fallback_config

# 检查 reasoning_effort 兼容性
warnings = validate_config("gpt-4.1-mini", "high")
# ['Provider openai_gpt4 does not support reasoning_effort; value high will be dropped']

# 检查 fallback 链的 vision 兼容性
warnings = validate_fallback_config({"gpt-4.1-*": ["deepseek-chat"]})
# ['Pattern gpt-4.1-* supports vision but no fallback model supports vision']
```

---

## Provider 翻译

同一个 `reasoning_effort` 参数，自动按 provider 翻译：

| 配置值 | DeepSeek V4 | OpenAI GPT-5 | Gemini 2.5 | GPT-4.x |
|--------|------------|--------------|------------|---------|
| `None` | 不发 | 不发 | 不发 | 不发 |
| `"disabled"` | `thinking.type=disabled` | `effort=minimal` | `effort=none` | 忽略 |
| `"high"` | 透传 | 透传 | 透传 | drop+warn |
| `"max"` | 透传 | clamp→`high` | clamp→`high` | drop+warn |

支持 11 个 provider 族：`deepseek` / `gemini_25` / `gemini_3` / `gemini` / `openai_gpt5` / `openai_gpt4` / `openai_o` / `doubao_seed` / `doubao` / `mimo` / `openai`

### v0.9.0 检测结果与严格模式
`detect_provider(model)` 返回 `ProviderDetection`，不是字符串；旧的字符串比较改用 `.family`，
还需用 `.matched` 判断是否命中 pattern：

```python
from llm_compat import detect_provider

detection = detect_provider("gpt-5-mini")
print(detection.family, detection.matched)  # openai_gpt5 True
```

未知模型返回 `family="openai", matched=False` 并记录 warning。

`LLMClient(..., strict_unknown_models=True)` 是构造参数，默认 `False`：

- 宽松：未知模型按 openai 族翻译 reasoning，JSON 使用 `json_schema`，文本 fallback 保留未知候选。
- 严格：未知模型丢弃 reasoning、JSON 降为 `json_object`；仅 vision fallback 移除未知候选，文本仍保留。
- 已知但不支持 vision 的模型始终从 vision fallback 链移除；这不替调用方决定主模型。

上游拒绝 `json_schema` 时该次请求降为 `json_object` 并告警；`json_object` 会注入 schema
prompt，Pydantic `schema=` 做本地校验，而只传 `json_schema=` 不做本地结构校验。

### `extra_body` 请求体契约

`extra_body` 是普通的 wire 字段，会原样保留在请求 body 顶层的 `extra_body` 对象中，不会被
展开、校验或过滤。这样可以直接透传 provider 的原生扩展字段；例如 Gemini 的 thinking 配置：

```python
result = await client.chat(
    "gemini-3.6-flash",
    messages,
    extra_body={
        "google": {
            "thinking_config": {
                "thinking_level": "low",
                "include_thoughts": True,
            }
        }
    },
)
```

请求体中的形状是：

```json
{
  "extra_body": {
    "google": {
      "thinking_config": {"thinking_level": "low", "include_thoughts": true}
    }
  }
}
```

注意不要照抄 Google 官方文档中针对 OpenAI Python SDK 的双层写法。SDK 调用需要外层
`extra_body` 容器，SDK 会把内层内容展开；llm-compat 直接发送 HTTP body，只需要单层：

```python
# 正确：llm-compat 直接发送这一层
extra_body={"google": {"thinking_config": {"thinking_level": "low"}}}

# 错误：这是 OpenAI SDK 的写法，会得到多一层嵌套
extra_body={"extra_body": {"google": {"thinking_config": {"thinking_level": "low"}}}}
```

第二种写法会被原样发送为 `{"extra_body": {"extra_body": {"google": ...}}}`，不会触发
llm-compat 的展开逻辑。

思考开关请使用 `reasoning_effort="disabled"`（或兼容别名 `"none"`），不要通过
直接 `**extra` 传 `thinking`；该字段会被防护逻辑丢弃并记录 warning。`extra_body` 中的
`thinking` 只是嵌套字段，不会覆盖顶层翻译结果。库会根据目标 provider 将
`reasoning_effort` 翻译成对应的 wire 字段，content fallback 切换模型时也会重新翻译。

### 自定义 Provider

代理服务重命名了模型时：

```python
from llm_compat import register_provider, set_custom_patterns

register_provider("my-proxy-ds-*", "deepseek")
set_custom_patterns({"my-ds-*": "deepseek", "my-gpt-*": "openai_gpt4"})

# 新 family 必须给出完整 caps；字段和值域见 caps.json schema/enums。
register_provider(
    "yi-*", "yi",
    caps={"disable_mode": "effort_none", "efforts": {"low", "medium", "high"},
          "supports_vision": False, "json_mode": "json_object"},
)
```

不传 `caps` 只表示复用已登记 family 的能力，不会创建新的能力记录。

---

## Collector 集成（可选）

Collector 是独立的 Sidecar 服务，跨项目自动收集拒绝事件，人工审核后提取敏感词，闭环回 pre-scan。

```
项目 A ──┐                                    ┌── 项目 A 加载新词表
项目 B ──┼── 拒绝事件上报 → Collector → 人工审核 ──┼── 项目 B 加载新词表
项目 C ──┘                  (SQLite)    加词    └── 项目 C 加载新词表
```

### 部署

```bash
docker network create llm-net
cd collector
echo "COLLECTOR_API_KEY=your-secret" > .env
docker compose up -d
```

### 接入

在 `LLMClient` 中添加三个参数：

```python
client = LLMClient(
    ...,
    content_fallbacks={...},
    collector_url="http://llm-compat-collector:8000",
    collector_project="my-project",
    collector_api_key="your-secret",
    refusal_keywords_url="http://llm-compat-collector:8000/words.txt",
    sensitive_words_url="http://llm-compat-collector:8000/words.txt",
)
```

项目容器需加入同一 Docker 网络：

```yaml
networks:
  llm-net:
    external: true
```

Collector 的任何故障都**不影响** chat 功能（fire-and-forget，不可达时静默跳过）。

### 日常运维

```bash
curl -s http://localhost:8234/stats | jq          # 查看拒绝统计
curl -s http://localhost:8234/words | jq          # 查看当前词表
curl -X POST http://localhost:8234/words \
  -H 'Content-Type: application/json' \
  -d '{"word": "敏感词"}'                          # 加词
curl -X DELETE http://localhost:8234/words/误报词   # 删词
```

### Collector API

| 端点 | 方法 | 鉴权 | 说明 |
|------|------|------|------|
| `/refusals` | POST | 需要 | 上报拒绝事件 |
| `/words` | GET | 不需要 | 获取当前词表（JSON，含 hash） |
| `/words.txt` | GET | 不需要 | 获取当前词表（纯文本，每行一词） |
| `/words` | POST | 需要 | 添加敏感词 |
| `/words/{word}` | DELETE | 需要 | 删除误报词 |
| `/stats` | GET | 不需要 | 拒绝统计 |

---

## 迁移指南（已有项目）

如果你的项目已有自建的 LLM 客户端，接入 llm-compat 时可以删除大量重复代码。

### 迁移策略

llm-compat 接管 HTTP 通信、重试、provider 翻译、JSON 清洗，项目只保留配置解析 + Prompt 模板 + 业务逻辑。

```
迁移前:  项目代码 = 配置 + Prompt + HTTP + 重试 + 翻译 + JSON清洗 + 熔断 + 并发 + 业务
迁移后:  项目代码 = 配置 + Prompt + 业务
         llm-compat = HTTP + 重试 + 翻译 + 结构化JSON + 并发 + 降级 + Hooks
```

### 可以删除的代码

| 功能 | 典型代码 | llm-compat 替代 |
|------|---------|-----------------|
| httpx 客户端管理 | `httpx.AsyncClient(...)` | `LLMClient` 内部管理 |
| 重试/退避逻辑 | `for attempt in range(max_retries)` | 内置智能重试 |
| Provider 翻译 | `if "deepseek" in model` | `providers.py` 自动检测 |
| JSON code fence 清洗 | `re.search(r'```json')` | `json_utils.py` |
| response_format 注入 | `"response_format": {"type": "json_schema"}` | `chat_json()` 自动适配 |
| JSON 解析重试 | `for i in range(max_retries)` | `self_correction=True` |
| 并发限制 | `asyncio.Semaphore(30)` | `max_concurrency=30` |
| 熔断器接入 | 自建 CircuitBreaker | `pre_request` hook |
| base64 图片编码 | `_build_image_content()` | `chat_image()` 内置 |

### 薄封装模式

项目级 LLMClient 变成 llm-compat 的薄封装，只保留配置解析：

```python
from llm_compat import LLMClient as BaseLLMClient, ChatResult

class LLMClient:
    """项目级封装：配置解析 + task_overrides"""

    def __init__(self, config: LLMConfig):
        self._base = BaseLLMClient(
            base_url=config.text.base_url,
            api_key=config.text.api_key,
            content_fallbacks={"deepseek-v4-pro": ["gemini-3-flash-preview"]},
            max_concurrency=30,
            on_error=lambda model, err: breaker.record_failure(),
            on_success=lambda model, ms: breaker.record_success(),
            pre_request=lambda model: breaker.can_execute(),
        )

    async def chat(self, task_name, messages, **kwargs) -> ChatResult:
        cfg = self._resolve_config(task_name)
        return await self._base.chat(model=cfg.model, messages=messages, **kwargs)
```

### 迁移检查清单

- [ ] 安装 llm-compat（v0.6.0+）
- [ ] 创建薄封装 LLMClient（保留配置解析，删除 HTTP/重试/翻译）
- [ ] `reasoning_effort: "none"` → `"disabled"`
- [ ] 删除重试逻辑、JSON 清洗、response_format 注入、并发信号量
- [ ] `chat_json(schema=..., self_correction=True)` 替代手动 JSON 处理
- [ ] 通过 `pre_request`/`on_error`/`on_success` hook 接入熔断器
- [ ] 更新错误处理为 `FatalError`/`TimeoutError`/`JSONParseError`/`ContentPolicyError`
- [ ] 可选：添加 `validate_config` 启动校验
- [ ] 可选：添加 `content_fallbacks` + Collector 集成
- [ ] 运行测试验证

---

## API 参考

### LLMClient 构造参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `base_url` | `str` | *必填* | API 基础 URL |
| `api_key` | `str` | *必填* | API Key |
| `timeout` | `httpx.Timeout` | 10s connect / 300s read | HTTP 超时 |
| `max_retries` | `int` | `3` | 可重试错误的重试次数 |
| `base_delay` | `float` | `1.0` | 首次重试延迟（秒） |
| `max_delay` | `float` | `60.0` | 最大退避延迟（秒） |
| `total_timeout` | `float` | `300.0` | 单次请求总超时（秒） |
| `content_fallbacks` | `dict[str, list[str]]` | `None` | 模型→fallback 链映射 |
| `refusal_detector` | `RefusalDetector` | `None` | 自定义拒绝检测函数 |
| `refusal_keywords` | `list[str]` | `None` | 追加拒绝关键词 |
| `refusal_keywords_url` | `str \| list[str]` | `None` | 从 URL 动态加载关键词 |
| `refusal_keywords_mode` | `Literal["extend", "replace"]` | `"extend"` | 内置句式与调用方词条合并方式 |
| `refusal_max_content_length` | `int` | `300` | 文本层判定的全文字符上限 |
| `refusal_head_window` | `int` | `120` | 文本层命中必须位于正文前 N 字 |
| `on_all_refused` | `Literal["raise", "return_best"]` | `"return_best"` | 链耗尽时救援推断层最佳候选或抛错 |
| `sensitive_detector` | `SensitiveDetector` | `None` | 前置敏感词检测器（手动词库） |
| `sensitive_words_url` | `str \| list[str]` | `None` | 从 URL 加载敏感词（纯文本格式） |
| `collector_url` | `str` | `""` | Collector 服务地址 |
| `collector_project` | `str` | `""` | 项目标识 |
| `collector_api_key` | `str` | `""` | Collector API Key |
| `max_concurrency` | `int` | `None` | 最大并发数 |
| `on_success` | `(str, int) → None` | `None` | 成功回调 (model, latency_ms) |
| `on_error` | `(str, Exception) → None` | `None` | 错误回调 (model, error) |
| `pre_request` | `(str) → bool` | `None` | 请求前置检查，返回 False 跳过 |
| `strict_unknown_models` | `bool` | `False` | 未知模型丢弃 reasoning、JSON mode 降为 `json_object`；vision fallback 移除未知候选 |

### ChatResult

| 字段 | 类型 | 说明 |
|------|------|------|
| `content` | `str` | 模型返回的文本（`str(result)` 直接使用） |
| `parsed` | `Any \| None` | `chat_json()` 时为解析后的对象（Pydantic 实例或 dict） |
| `usage` | `TokenUsage` | prompt_tokens / completion_tokens / total_tokens；`reasoning_tokens` 取上游 `completion_tokens_details.reasoning_tokens`，缺失时为 `0` |
| `latency_ms` | `int` | 请求延迟（毫秒） |
| `request_id` | `str` | 请求追踪 ID |
| `model` | `str` | 实际使用的模型名 |
| `provider` | `str` | provider 族 |
| `fallback_from` | `str \| None` | 降级时为原始模型，未降级时为 None |
| `fallback_chain` | `list[str]` | 降级时尝试过的模型链 |
| `trace` | `CallTrace \| None` | v0.6.0 模型级调用轨迹；旧代码不传时保持 `None` |
| `refusal_suspected` | `bool` | 链耗尽救援推断层候选时为 `True` |
| `refusal_evidence` | `RefusalEvidence \| None` | 触发拒绝或救援的结构化证据 |
| `finish_reason` | `str \| None` | 原样透传上游 `choices[0].finish_reason`；字段缺失时为 `None`，不猜测为 `"stop"` |

### CallTrace：成功与失败的统一事实

`chat()` 和 `chat_json()` 成功时通过 `ChatResult.trace` 返回轨迹。进入共享调用编排后
发生的稳定运行错误通过 `LLMCallError.trace` 返回同一种轨迹：

```python
from llm_compat import LLMCallError

try:
    result = await client.chat_json(model, messages, schema=TagResult)
    trace = result.trace
except LLMCallError as error:
    trace = error.trace
    print(error.error_kind, error.http_status)

if trace is not None:
    record = trace.to_dict()  # 只含安全标量、字典和列表
```

几个模型字段不能混用：

| 概念 | 位置 | 含义 |
|------|------|------|
| requested model | `CallTrace.requested_model` | 调用方最初请求的模型，即使后来被预检跳过也不变 |
| skipped model | `route_decisions[action="skipped"]` | 被路由决策跳过，没有向上游发请求 |
| attempted model | `model_attempts[*].model` | 确实向上游发出过请求；每个 `_ChatRequest` 一条 |
| final model | `CallTrace.final_model` | 成功模型，或终态错误前最后尝试的模型 |
| final outcome | `CallTrace.final_outcome` | 整个逻辑调用结果，如 `success`、`json_parse`、`content_policy` |

`ModelAttempt.outcome="response_received"` 只说明收到上游响应。对于 `chat_json()`，响应
仍可能解析失败；解析终态只看 `CallTrace.final_outcome`。被判拒的 attempt 带
`detection_layer`（该次实际判定层）和该次上游 `finish_reason`；未被判拒的正常 attempt
这两个字段为 `None`。trace 不包含 prompt、messages、
payload、响应正文、headers、API key 或原始异常。公共对象是 frozen dataclass，且内部 tuple
不可变；超过 100 条模型事件时 `truncated=True` 并记录 `dropped_events`。

### 错误类型

| 错误 | 父类 | 说明 | 是否重试 |
|------|------|------|---------|
| `LLMError` | `Exception` | 所有错误的基类 | — |
| `LLMCallError` | `LLMError` | 稳定运行错误父类，提供 `error_kind` / `http_status` / `trace` | — |
| `RetryableError` | `LLMCallError` | 可重试错误（502/503/429/网络错误） | 是 |
| `TimeoutError` | `RetryableError` | 请求超时 | 否 |
| `TruncationError` | `RetryableError` | 输出被截断 | 否 |
| `FatalError` | `LLMCallError` | 不可恢复错误（400/401/403/404） | 否 |
| `JSONParseError` | `LLMCallError` | JSON 解析失败（兼容保留 raw_content, model, request_id） | 否 |
| `ContentPolicyError` | `LLMCallError` | 所有模型拒绝（含 `evidence` / `attempt_layers`） | 否 |
| `SkipRequestError` | `LLMError` | pre_request hook 返回 False | 否 |

稳定 `error_kind` 包括 `invalid_request`、`authentication`、`permission_denied`、
`model_not_found`、`rate_limited`、`upstream_server_error`、`timeout`、`network_error`、
`content_policy`、`unsupported_response_format`、`json_parse` 和 `unknown`。未知异常不会被
blanket-wrap 成 `LLMCallError`，仍保持原异常类型。

v0.6.0 只提供模型级事实。transport 内每次 HTTP retry、脱敏错误摘要、`on_trace` hook、
新统计口径属于阶段 2；消费项目迁移属于阶段 3。当前版本不改变 timeout/retry 行为、
`LLMStats.total_calls` 口径或 `chat_stream()` 的 trace 支持范围。

---

## FAQ

### Q: 可以只用 chat() 不用其他功能吗？

可以。content_fallbacks、Collector、Hook 全是可选的，不配就不启用。最小配置只需 `base_url` + `api_key`。

### Q: chat_json() 支持 content fallback 吗？

支持（v0.4.0+，v0.5.0 起 `chat_json()` 也支持 `sensitive_words_url` 前置检测）。`chat()` 和 `chat_json()` 共享同一套 content fallback 逻辑。fallback 切换模型时会自动为新模型选择正确的 json_mode。

### Q: Collector 挂了会影响 chat 吗？

不会。上报是 fire-and-forget，Collector 不可用时静默跳过。

### Q: 多个项目怎么区分拒绝来源？

通过 `collector_project` 参数。每个项目传自己的名字（如 `"video-api"`），Collector 统计中可按项目筛选。

### Q: 需要改业务逻辑吗？

不需要。只改 `LLMClient` 初始化参数。所有 API 调用方式和返回类型不变。

---

## 完整配置示例

```python
import os
from llm_compat import LLMClient

async with LLMClient(
    base_url=os.environ["LLM_BASE_URL"],
    api_key=os.environ["LLM_API_KEY"],
    max_retries=3,
    total_timeout=300.0,
    max_concurrency=30,
    content_fallbacks={
        "deepseek-v4-pro": ["gemini-3-flash-preview", "gemini-2.5-flash"],
        "deepseek-v4-flash": ["gemini-3.1-flash-lite-preview"],
    },
    sensitive_words_url=os.environ.get("LLM_SENSITIVE_WORDS_URL", ""),
    collector_url=os.environ.get("LLM_COLLECTOR_URL", ""),
    collector_project=os.environ.get("LLM_COLLECTOR_PROJECT", ""),
    collector_api_key=os.environ.get("LLM_COLLECTOR_API_KEY", ""),
    refusal_keywords_url=os.environ.get("LLM_REFUSAL_KEYWORDS_URL", ""),
) as client:
    result = await client.chat("deepseek-v4-pro", messages)
```
