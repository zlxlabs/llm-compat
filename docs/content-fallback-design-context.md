# 内容审查降级（Content Policy Fallback）— 设计背景文档

> 供新 session 开发此功能时参考。包含：问题定义、调研结论、现有代码基础、推荐方案方向。

## 1. 问题定义

### 业务场景

10+ Python 项目通过 New API（OpenAI 兼容代理）接入多家 LLM。成本优先策略：
- **首选**：DeepSeek 等国内模型（成本极低）
- **问题**：中国政策要求的敏感词审查，导致部分请求被拒绝
- **期望**：被拒绝时自动降级到海外模型（GPT、Gemini 等），牺牲成本换取可用性

### 难点

国内模型触发内容审查时的行为**不统一**：

| 类型 | 行为 | 检测难度 |
|------|------|---------|
| HTTP 错误 | 返回 400/403 + error message（"content filtered"、"sensitive content"） | 容易 — 错误码 + message pattern |
| 静默拒绝 | 返回 200 + 正常结构，但 content 是拒绝文本（"我无法回答该问题"、"涉及敏感内容"） | 较难 — 需要内容检测 |
| 部分输出 | 返回 200 + 截断/替换后的内容 | 最难 — 不一定能检测 |

第二种（200 + 拒绝文本）是最常见的情况，也是市面方案都没覆盖好的痛点。

## 2. 市面方案调研结论

### LiteLLM `content_policy_fallbacks`

LiteLLM 内置了 content policy fallback 功能：

```yaml
# LiteLLM Proxy config.yaml
content_policy_fallbacks: [{"deepseek-chat": ["gpt-4o-mini"]}]
```

- 当检测到 `ContentPolicyViolationError`（HTTP 4xx 错误码级别）时自动降级
- **局限**：只处理 HTTP 错误码，**不处理 200 + 拒绝文本**
- **架构冲突**：LiteLLM Proxy 与我们的 New API 是同层竞品，不能叠加使用
- LiteLLM SDK 模式要求直连各家 API，绕过 New API

### New API (One API) 渠道降级

- 有渠道级别的故障转移（整个渠道挂了才切）
- 有定期渠道测试（`CHANNEL_TEST_FREQUENCY`）
- **没有**请求级别的内容审查感知 fallback
- 要实现需魔改 One API 源码

### 结论

两个方案都**不能满足需求**，核心差距在于：
1. 都不处理 200 + 拒绝文本（最常见场景）
2. 都有架构兼容问题

## 3. 推荐方案：在 llm-compat 客户端层实现

### 架构定位

```
应用
  → llm-compat（检测拒绝 → 换模型重试）
    → httpx
      → New API（路由/鉴权/负载均衡）
        → DeepSeek / GPT / Gemini ...
```

在客户端层做 fallback 的优势：
1. 能检测 200 + 拒绝文本（有完整响应内容）
2. 不改网关层，零架构风险
3. 与现有 provider 翻译层天然集成
4. 应用侧有完整上下文，可做更智能的判断

### 初步 API 设想

```python
# 使用方式
response = await client.chat(
    model="deepseek-v4",
    messages=[...],
    content_fallbacks=["gpt-4o-mini", "claude-sonnet"],  # 降级链
)

# response.model 会反映实际使用的模型
# response.provider 会反映实际使用的 provider
```

### 检测机制需要覆盖

1. **HTTP 错误码**：400/403 + 已知 content filter error pattern
2. **响应文本**：200 但内容匹配拒绝模式（中文/英文关键词）
3. **自定义检测**：允许用户传入自定义检测函数

## 4. 现有代码基础

### 已有模块（可直接复用/扩展）

| 模块 | 路径 | 相关能力 |
|------|------|---------|
| `errors.py` | `src/llm_compat/errors.py` | 错误分类体系，`classify_error()` 按 HTTP 状态码分类。**注意**：当前 400 被归为 `FatalError` 不重试，content policy 的 400 需要特殊处理 |
| `retry.py` | `src/llm_compat/retry.py` | 重试逻辑，指数退避 + jitter + Retry-After。content fallback 是**换模型重试**，与现有的**同模型重试**是不同层次 |
| `providers.py` | `src/llm_compat/providers.py` | `detect_provider()` 按模型名 fnmatch 识别 provider family，`build_request_payload()` 翻译参数。fallback 到不同模型时需要重新翻译 |
| `client.py` | `src/llm_compat/client.py` | `LLMClient.chat()` 主入口，`_request()` 发送请求。fallback 逻辑需要在 `chat()` 层面包装 |
| `_types.py` | `src/llm_compat/_types.py` | `ChatResult` 包含 `model`、`provider` 字段，降级后需要反映实际使用的模型 |
| `json_utils.py` | `src/llm_compat/json_utils.py` | JSON 响应清洗（code fence / bare list）|

### errors.py 当前分类逻辑

```python
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_FATAL_STATUS_CODES = frozenset({400, 401, 403, 404})
```

400 当前是 `FatalError`（不重试）。content policy violation 返回的 400 需要**从 Fatal 中拆出**，归为新的 `ContentPolicyError`，触发 fallback 而不是直接失败。

### 参考项目中的相关代码

#### VideoTranscriptAPI — `providers.py`

路径：`/home/zlx/projects/personal/VideoTranscriptAPI/src/video_transcript_api/llm/providers.py`

这是 llm-compat 的前身实现，包含：
- `detect_provider()` + `_translate()` 的完整逻辑
- `_FAMILY_CAPABILITIES` provider 能力表
- `describe_from_payload()` 日志描述
- `set_custom_patterns()` 自定义 pattern 注入

与 llm-compat 的 `providers.py` 基本一致（llm-compat 是从这里提取的）。

**敏感词处理**：该项目中有涉及 refusal/filter 的代码，参考路径：

```
grep -r 'refusal\|filter\|sensitive\|moderation' \
  /home/zlx/projects/personal/VideoTranscriptAPI/src/ --include='*.py'
```

#### Memos Auto — `llm_client.py`

路径：`/home/zlx/projects/personal/Memos_auto_with_AI/backend/core/clients/llm_client.py`

这是 llm-compat 的第一个消费者，展示了典型的集成模式：
- 薄包装层，委托 `llm_compat.LLMClient` 处理 HTTP/重试/翻译
- 项目特定的配置解析（task_overrides、text/image 切换）
- Prompt 模板加载

**敏感词处理**：该项目中有涉及 refusal/filter 的代码，参考路径：

```
grep -r 'refusal\|filter\|sensitive\|moderation' \
  /home/zlx/projects/personal/Memos_auto_with_AI/backend/ --include='*.py'
```

## 5. 设计考量

### 需要决策的问题

1. **检测策略**：
   - 内置中文/英文拒绝关键词列表？还是让用户自定义？还是两者都支持？
   - 关键词列表如何维护和更新？
   - 是否需要支持正则匹配？

2. **fallback 与 retry 的关系**：
   - 现有 `retry.py` 是同模型重试（网络错误、限流）
   - content fallback 是换模型重试
   - 两者应该如何组合？先 retry 再 fallback？还是 content policy error 直接跳 fallback？

3. **流式响应的处理**：
   - 非流式：拿到完整响应后检测，如果被拒绝则用 fallback 模型重发
   - 流式：更复杂，可能需要先发一个非流式探测请求？或者积累部分 chunk 后判断？

4. **成本与延迟**：
   - fallback 意味着至少两次 API 调用（失败的 + 成功的）
   - 是否需要记录 fallback 频率，帮助用户优化 prompt 或选择模型？

5. **日志与可观测性**：
   - 需要清晰记录：哪个模型被拒绝、拒绝原因、降级到哪个模型
   - `LLMStats` 是否需要新增 fallback 相关指标？

### 不做的事情

- **不做 prompt 改写**：不尝试修改 prompt 来绕过审查（这是用户的责任）
- **不做内容预审**：不在发送前检测 prompt 是否可能触发审查
- **不做路由优化**：不根据内容自动选择首选模型（这是更高层的策略）
