# RFC: llm-compat 功能扩展需求

> 来源: AI_Information_processor 迁移评估
> 日期: 2026-05-13
> 状态: 需求 1/3/4 已实现（v0.3.0），需求 2/5 待定

## 背景

llm-compat 当前覆盖了 chat completion 的核心链路（重试、provider 翻译、内容审查降级、JSON 清洗、敏感词检测）。但在将 AI_Information_processor 项目迁移过来的过程中，发现以下功能缺口需要补齐，才能让下游项目真正删除自建的 LLM 客户端代码。

这些需求不是某个项目的特殊逻辑，而是 **多个项目共同面临的通用问题**，适合放在共享层解决。

---

## 需求 1: JSON 结构化输出（高优先级）

### 现状

当前 `chat_json()` 的实现：
1. 调用 `chat()` 获取纯文本响应（不设 `response_format`）
2. 后处理：剥离 code fence → `json.loads` → Pydantic 校验
3. 失败抛 `JSONParseError`

### 问题

- **没有利用 API 层面的结构化输出能力**。OpenAI/Gemini 等支持 `response_format: {"type": "json_schema"}` 或 `{"type": "json_object"}`，能从 API 层面约束模型输出合法 JSON，成功率远高于纯靠模型自觉。
- **不同 provider 对结构化输出的支持不同**，需要自动适配：

| Provider | json_schema | json_object | 均不支持 |
|----------|-------------|-------------|---------|
| OpenAI GPT-4o/4.1 | ✅ | ✅ | - |
| OpenAI o-series | ❌ | ✅ | - |
| Gemini 2.5+ | ✅ | ✅ | - |
| DeepSeek | ❌ | ✅ | - |
| 豆包 (Doubao) | ❌ | ❌ | ✅ |

- **解析失败时没有 Self-Correction 机制**。当前直接抛异常，调用方要自己处理重试。

### 期望方案

#### 1.1 三级降级策略

```
json_schema 模式（API 层面约束）
    ↓ 模型不支持 / API 报错
json_object 模式（API 层面约束 + Schema 注入 Prompt）
    ↓ 模型不支持
纯文本模式（Schema 注入 Prompt + 后处理清洗）
```

llm-compat 根据 `FAMILY_CAPABILITIES` 自动选择最高级别的可用模式，调用方无感知。

#### 1.2 在 `FAMILY_CAPABILITIES` 中新增字段

```python
_FAMILY_CAPABILITIES = {
    "openai_gpt4": {
        ...,
        "json_mode": "json_schema",     # 最高支持级别
    },
    "openai_o": {
        ...,
        "json_mode": "json_object",
    },
    "deepseek": {
        ...,
        "json_mode": "json_object",
    },
    "doubao": {
        ...,
        "json_mode": "none",            # 不支持任何结构化输出
    },
    "gemini_25": {
        ...,
        "json_mode": "json_schema",
    },
    ...
}
```

#### 1.3 `chat_json()` 增强

```python
result = await client.chat_json(
    model="deepseek-v4",
    messages=[...],
    schema=MyPydanticModel,         # Pydantic model（现有）
    json_schema={...},              # 可选：原始 JSON Schema dict（用于 json_schema 模式）
    max_retries=3,                  # JSON 解析失败的重试次数
    self_correction=True,           # 解析失败时把错误反馈给模型重试
)
```

**Self-Correction 流程**（解析失败时）：
1. 将模型上一轮的错误输出 + 解析错误信息追加到 messages
2. 重新调用 API
3. 最多重试 `max_retries` 次
4. 全部失败后抛 `JSONParseError`（附带最后一次的原始输出）

#### 1.4 json_schema 模式的 payload 构建

当 provider 支持 `json_schema` 时：

```python
payload = {
    "model": "gpt-4.1-mini",
    "messages": [...],
    "response_format": {
        "type": "json_schema",
        "json_schema": {
            "name": "MySchema",
            "strict": True,
            "schema": { ... }   # 从 Pydantic model 或 json_schema 参数生成
        }
    }
}
```

当降级到 `json_object` 时：

```python
payload = {
    "model": "deepseek-v4",
    "messages": [
        ...,
        # 在最后一条 user message 末尾追加 Schema 说明
    ],
    "response_format": {"type": "json_object"}
}
```

#### 1.5 统计指标

在 `LLMStats` 中新增：

```python
json_schema_calls: int = 0          # json_schema 模式调用次数
json_object_calls: int = 0          # json_object 降级次数
json_none_calls: int = 0            # 纯文本降级次数
json_parse_failures: int = 0        # 解析失败次数
json_self_correction_success: int = 0  # Self-Correction 成功次数
```

### 受益项目

- AI_Information_processor: orchestrator 的 7 个 AI 任务全部用 JSON Schema 输出
- url-parse-api: URL 解析结果的结构化提取
- Memos_auto_with_AI: AI 生成结构化 memo
- 所有需要 LLM 返回结构化数据的项目

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

## 需求 3: 熔断器 / 健康检查 Hook（低优先级，需讨论）

### 现状

llm-compat 没有熔断器。AI_Information_processor 自建了 `CircuitBreaker`（三态: CLOSED/OPEN/HALF_OPEN），全局共享，连续失败 5 次触发熔断，3 分钟后半开恢复。

### 讨论点

熔断器是否适合放在 llm-compat？两种思路：

**方案 A: llm-compat 内置熔断器**

优点：所有项目自动获得熔断保护，不用重复实现。
缺点：
- 熔断策略（阈值、恢复时间）因项目而异
- 熔断通知（企微、飞书）是项目特有的
- llm-compat 已有 `content_fallbacks` 降级链，与熔断器的职责有重叠

**方案 B: llm-compat 提供 Hook 接口（推荐）**

不内置熔断器，但暴露生命周期 hook，让项目自己接入：

```python
client = LLMClient(
    ...,
    on_success=lambda model, latency_ms: breaker.record_success(),
    on_error=lambda model, error: breaker.record_failure(),
    pre_request=lambda model: breaker.can_execute(),   # 返回 False 则跳过请求
)
```

或者更结构化：

```python
from llm_compat import RequestHook

class CircuitBreakerHook(RequestHook):
    def pre_request(self, model: str) -> bool:
        return self.breaker.can_execute()

    def on_success(self, model: str, latency_ms: int) -> None:
        self.breaker.record_success()

    def on_error(self, model: str, error: Exception) -> None:
        self.breaker.record_failure()

client = LLMClient(..., hooks=[CircuitBreakerHook()])
```

这样 llm-compat 保持轻量，项目按需接入熔断器、监控、告警等。

### 受益项目

- AI_Information_processor: 现有熔断器可以通过 hook 接入
- 其他长时间运行的服务型项目

---

## 需求 4: 并发控制（低优先级，需讨论）

### 现状

AI_Information_processor 用 `asyncio.Semaphore` 控制 AI API 并发数（默认 30）。llm-compat 没有并发控制。

### 讨论点

并发控制是否应该放在 llm-compat？

**方案 A: llm-compat 内置**

```python
client = LLMClient(..., max_concurrency=30)
```

优点：简单，一行配置。
缺点：多个 LLMClient 实例的并发独立计算，无法全局控制。

**方案 B: 通过 Hook 接入（与需求 3 合并）**

项目层创建信号量，通过 `pre_request` hook 实现等待。

**方案 C: 不做，留给项目层**

大部分项目不需要精细的并发控制。需要的项目自己在外层包一层 semaphore 即可。

### 建议

倾向方案 A，因为 `max_concurrency` 是一个足够通用的需求，实现成本低（内部一个 semaphore），且能防止调用方不小心打爆 API。

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

## 优先级总结

| # | 需求 | 优先级 | 估算工作量 | 阻塞迁移 |
|---|------|--------|-----------|---------|
| 1 | JSON 结构化输出 | **高** | M | **是** — AIClient 核心功能 |
| 2 | Embedding API | **中** | S | 否 — event_cluster 可暂不迁移 |
| 3 | 熔断器 Hook | **低** | S | 否 — 项目层保留现有实现 |
| 4 | 并发控制 | **低** | S | 否 — 项目层保留现有实现 |
| 5 | Token 计数 | **低** | S | 否 — 项目层保留现有实现 |

**迁移阻塞关系**: 只有需求 1（JSON 结构化输出）是迁移的硬前置条件。其余需求可以在迁移完成后逐步补齐，项目层暂时保留现有实现。

---

## 迁移路径建议

```
Phase 1: llm-compat 补齐 JSON 结构化输出（需求 1）
    ↓
Phase 2: AI_Information_processor 的 AIClient 改为 llm-compat 薄封装
         - 删除: HTTP 客户端管理、重试逻辑、JSON 清洗、敏感词 fallback
         - 保留: 熔断器、并发信号量、分组模式、Settings 集成
         - 代码量: ~600 行 → ~150 行
    ↓
Phase 3: 逐步补齐 embedding、hook、并发控制
         - 每个需求独立 PR，不阻塞主流程
    ↓
Phase 4: 其他项目陆续迁移（复用同一套 llm-compat）
```
