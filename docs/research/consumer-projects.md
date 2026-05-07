# 消费者项目调研

记录当前使用 LLM 的项目及其调用模式，用于确定 llm-compat 的 API 设计。

## 已分析的项目

### 1. Memos Auto (Memos_auto_with_AI)

**用途**: 基于 Memos 笔记的自动化工作流平台（打标、评论、摘要、推送等）

**当前 LLM 实现**: `backend/core/clients/llm_client.py` (~538 行)

**调用模式**:
- `chat(messages, task_name, use_image)` → str
- `chat_json(messages, schema, task_name)` → Pydantic model
- `chat_stream(messages, task_name)` → AsyncIterator[str]
- `chat_image(text, image_data, media_type)` → str

**配置模式**:
- 三层 LLM 配置：text / image / embedding
- per-task overrides：按任务名覆盖 model / reasoning_effort / max_tokens
- Prompt 模板：独立 prompts.yaml + PromptManager

**当前问题**:
- `reasoning_effort` 使用 `Literal["none", "low", "medium", "high"]`，太死
- 不支持 DeepSeek V4 的 `thinking` 对象
- 不支持 `"max"` 等新值
- 无 provider 检测，参数直接透传

**使用的模型**: DeepSeek (deepseek-chat, deepseek-v4-flash), GPT-4o-mini, Doubao Vision

**特殊需求**:
- 多模态图片理解（单图 + 多图）
- 异步 httpx
- 连接池复用（按 base_url 缓存 client）

### 2. VideoTranscriptAPI

**用途**: 视频转录 + AI 摘要 API

**当前 LLM 实现**: 已有 `providers.py` provider 翻译层（本包设计的参考来源）

**调用模式**:
- 同步 httpx
- chat + chat_json
- 无流式、无多模态

**配置模式**:
- 按任务配置 model + reasoning_effort
- provider_patterns 支持自定义模型名映射

**已解决的问题**:
- Provider 检测 (fnmatch)
- thinking 参数翻译（disabled → 各家写法）
- 启动时配置校验 + 不兼容 warn

### 3. 其他项目（待详细分析）

基于 10+ 项目的共性需求估计：

| 需求 | 频率 |
|------|------|
| 基础 chat → str | 100% |
| JSON 结构化输出 | ~80% |
| 流式输出 | ~30% |
| 多模态图片 | ~20% |
| 同步调用 | ~50% |
| 异步调用 | ~50% |
| per-task 配置覆盖 | ~40%（项目级逻辑，不属于包） |
| Prompt 模板 | ~60%（项目级逻辑，不属于包） |

## API 设计影响

基于以上调研，llm-compat 需要支持：

1. **同步 + 异步** — 必须双模式（或至少异步，让同步项目用 asyncio.run）
2. **chat / chat_json / chat_stream / chat_image** — 四种调用模式
3. **model 作为调用参数** — 不绑定 client（Memos 的 text/image 切换需要）
4. **reasoning_effort 在调用时传入** — 不同任务可能用不同 effort

不需要包含：
- 配置管理（各项目自定义）
- Prompt 模板管理（各项目自定义）
- per-task overrides 解析（各项目自定义）
- Embedding 调用（各项目用法差异大，且无兼容性问题）
