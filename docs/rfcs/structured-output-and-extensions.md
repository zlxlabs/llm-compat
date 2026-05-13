# RFC: llm-compat 功能扩展需求

> 来源: AI_Information_processor 迁移评估
> 日期: 2026-05-13
> 状态: 需求 1/3/4 已实现（v0.3.0），需求 2/5 待定

## 背景

llm-compat 作为多项目共享的 LLM 工具包，已覆盖 chat completion 的核心链路（重试、provider 翻译、内容审查降级、JSON 清洗、敏感词检测）。在将 AI_Information_processor 项目迁移过来的过程中，梳理了功能缺口和已实现的能力对照。

---

## 需求 1: JSON 结构化输出 -- 已实现 (v0.3.0)

### 实现概要

`chat_json()` 已支持完整的结构化输出能力：

- **两级自动降级**: `json_schema` → `json_object`，根据 `FAMILY_CAPABILITIES` 中的 `json_mode` 字段自动选择
- **json_schema 报错降级**: `_is_format_error()` 检测 400 错误中的 `response_format`/`json_schema`/`unsupported` 关键词，自动降级到 `json_object`
- **Self-Correction**: `self_correction=True` 时，解析失败会将错误输出+错误信息追加到 messages 重试
- **Pydantic Schema 转换**: `pydantic_to_json_schema()` 自动从 Pydantic model 生成 JSON Schema（含 `additionalProperties: false` + title 清理，适配 OpenAI strict 模式）
- **统计指标**: `LLMStats` 已有 `json_schema_calls`、`json_object_calls`、`json_parse_failures`、`json_self_correction_success`

### API 签名

```python
result = await client.chat_json(
    model="deepseek-v4",
    messages=[...],
    schema=MyPydanticModel,         # Pydantic model -> 自动选 json_schema 或 json_object
    json_schema={...},              # 可选：原始 JSON Schema dict（强制 json_schema 模式）
    max_retries=2,                  # Self-Correction 重试次数
    self_correction=False,          # 是否启用 Self-Correction
)
```

### 各 Provider 的 json_mode 配置

| Provider | json_mode | 说明 |
|----------|-----------|------|
| openai (默认) | json_schema | |
| openai_gpt4 | json_object | GPT-4 系列不支持 strict json_schema |
| openai_gpt5 | json_schema | |
| openai_o | json_schema | o-series |
| deepseek | json_object | |
| gemini_25 | json_schema | |
| gemini_3 | json_schema | |
| gemini (旧版) | json_object | |
| doubao | json_schema | |
| doubao_seed | json_schema | |

### 迁移影响

AI_Information_processor 的 `AIClient.complete()` 中以下代码可删除：
- `_complete_with_json_object_mode()` 整个方法（Self-Correction 重试逻辑）
- `json_utils.py` 中的 `schema_to_prompt_instruction()`、`validate_json_response_fields()`
- `_json_mode_stats` 统计字典
- `response_format` 参数的手工构建逻辑

---

## 需求 2: Embedding API 支持（中优先级）

### 现状

llm-compat 不支持 embedding。项目直接用 `AsyncOpenAI` 调用 embedding API。

### 问题

- Embedding API 同样存在 provider 差异（模型名、维度参数、API 路径）
- 需要独立的 API 地址和密钥配置（与 chat completion 解耦）
- 缺少重试和错误处理

### 期望方案

```python
client = LLMClient(
    base_url="https://api.openai.com/v1",
    api_key="sk-xxx",
    # 可选：embedding 独立配置（不设则复用 base_url/api_key）
    embedding_base_url="https://embedding-api.example.com/v1",
    embedding_api_key="sk-embed-xxx",
)

# 单条
vector = await client.embed("text-embedding-3-small", "hello world", dimensions=512)
# vector: EmbeddingResult(embedding=[0.1, 0.2, ...], usage=TokenUsage(...))

# 批量
vectors = await client.embed_batch(
    "text-embedding-3-small",
    ["hello", "world"],
    dimensions=512,
)
# vectors: list[EmbeddingResult]
```

### 设计要点

- Embedding 客户端独立于 chat 客户端（独立的 base_url/api_key）
- 自动重试（复用现有 `retry.py`）
- 批量接口控制单次请求的 token 上限，超出自动分批
- 返回 `EmbeddingResult` dataclass（embedding 向量 + usage）

### 受益项目

- AI_Information_processor: 事件聚类的 embedding 生成
- 未来任何需要语义搜索、相似度计算的项目

---

## 需求 3: 生命周期 Hook -- 已实现 (v0.3.0)

### 实现概要

采用回调函数方式（方案 B 的轻量版），已集成到 `BaseClient.__init__()`:

```python
client = LLMClient(
    ...,
    on_success: Callable[[str, int], None]        # (model, latency_ms)
    on_error: Callable[[str, Exception], None]     # (model, error)
    pre_request: Callable[[str], bool]             # 返回 False 抛 SkipRequestError
)
```

- `pre_request` 返回 `False` 时抛出 `SkipRequestError`（已导出）
- Hook 异常被捕获并 log warning，不影响主流程
- `chat()` 和 `chat_json()` 均在入口/出口调用 hook

### 迁移影响

AI_Information_processor 的 `CircuitBreaker` 可通过 hook 接入 llm-compat，无需改动熔断器本身：
```python
client = LLMClient(
    ...,
    pre_request=lambda model: breaker.can_execute(),
    on_success=lambda model, ms: breaker.record_success(),
    on_error=lambda model, e: breaker.record_failure(),
)
```

---

## 需求 4: 并发控制 -- 已实现 (v0.3.0)

### 实现概要

`LLMClient` 构造函数新增 `max_concurrency` 参数：

```python
client = LLMClient(..., max_concurrency=30)
```

- 内部懒加载 `asyncio.Semaphore`
- 在 `_request()` 层面控制并发（覆盖 chat/chat_json/chat_stream 所有调用）
- 不设则无限制

### 迁移影响

AI_Information_processor 的 `AIClient` 中手动管理的 `self.semaphore = asyncio.Semaphore(settings.ai_concurrency)` 可删除，改用 `max_concurrency` 参数。

---

## 需求 5: Token 计数工具（低优先级）

### 现状

AI_Information_processor 用 `tiktoken` 做 token 计数和长文本截断。llm-compat 没有这个功能。

### 期望方案

```python
from llm_compat.tokens import count_tokens, truncate_to_tokens

count = count_tokens("hello world", model="gpt-4o")       # -> 2
text = truncate_to_tokens(long_text, max_tokens=8000, model="gpt-4o")
```

### 讨论点

- `tiktoken` 是一个较重的依赖（~30MB），是否作为可选依赖？
- 不同 provider 的 tokenizer 不同（OpenAI 用 tiktoken，其他 provider 的 tokenizer 不公开）
- 可以只支持 OpenAI tokenizer 作为近似估算

### 建议

作为可选功能 `llm-compat[tokens]`，不强制安装 tiktoken。

---

## 状态总结

| # | 需求 | 状态 | 阻塞迁移 |
|---|------|------|---------|
| 1 | JSON 结构化输出 | **已实现** (v0.3.0) | 已解除 |
| 2 | Embedding API | **待定** | 否 — event_cluster 可暂不迁移 |
| 3 | 生命周期 Hook | **已实现** (v0.3.0) | 已解除 |
| 4 | 并发控制 | **已实现** (v0.3.0) | 已解除 |
| 5 | Token 计数 | **待定** | 否 — 项目层保留现有实现 |

**迁移阻塞已全部解除**: 需求 1/3/4 已在 v0.3.0 实现，AIClient → llm-compat 薄封装的迁移可以立即开始。需求 2/5 为增量优化，不阻塞主迁移。

---

## 迁移路径

```
Phase 1: (已完成) llm-compat v0.3.0 补齐 JSON 结构化输出、Hook、并发控制
    ↓
Phase 2: AI_Information_processor 的 AIClient 改为 llm-compat 薄封装
         - 删除: HTTP 客户端管理(AsyncOpenAI)、重试逻辑、JSON 清洗(json_utils.py)、
                 敏感词 fallback、并发信号量、json_object 降级逻辑
         - 保留: 熔断器(通过 hook 接入)、分组模式(任务→模型映射)、
                 Settings 集成、Token 计数/截断
         - 代码量: ~600 行 → ~150 行
    ↓
Phase 3: 逐步补齐 embedding (需求 2)、token 工具 (需求 5)
         - 每个需求独立 PR，不阻塞主流程
    ↓
Phase 4: 其他项目陆续迁移（复用同一套 llm-compat）
```
