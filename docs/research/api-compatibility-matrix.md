# 多 Provider API 兼容性矩阵

基于 2026-05 各家官方文档的实际调研结果。

## 调研来源

- OpenAI: https://developers.openai.com/api/docs/guides/reasoning
- DeepSeek V4: https://api-docs.deepseek.com/zh-cn/api/create-chat-completion
- Gemini (OpenAI 兼容模式): https://ai.google.dev/gemini-api/docs/openai

## reasoning_effort 参数

三家都支持 `reasoning_effort` 作为 **顶层参数**（Chat Completions API），这是 OpenAI 协议的标准字段。

### 值域对比

| 值 | OpenAI GPT-5.x | OpenAI o-series | DeepSeek V4 | Gemini 2.5 | Gemini 3.x |
|----|----------------|-----------------|-------------|------------|------------|
| `none` | — | — | — | ✅ 关闭思考 | ❌ 关不掉 |
| `minimal` | ✅ 最低档 | — | ❌ 映射→high | — | ✅ 最低档 |
| `low` | ✅ | ✅ | ⚠️ 映射→high | ✅ | ✅ |
| `medium` | ✅ (默认) | ✅ | ⚠️ 映射→high | ✅ | ✅ |
| `high` | ✅ | ✅ | ✅ (默认) | ✅ | ✅ |
| `xhigh` | — | — | ⚠️ 映射→max | — | — |
| `max` | — | — | ✅ | — | — |

### 关闭思考方式

| Provider | 方式 | 请求体字段 |
|----------|------|-----------|
| OpenAI GPT-5.x | 只能降到 `minimal`，无法完全关闭 | `reasoning_effort: "minimal"` |
| OpenAI GPT-4.x | 不支持 reasoning，天然无思考 | 不发 reasoning_effort |
| DeepSeek V4 | 独有 `thinking` 对象 | `thinking: {"type": "disabled"}` |
| Gemini 2.5 | reasoning_effort 设为 none | `reasoning_effort: "none"` |
| Gemini 3.x | 只能降到 `minimal`，Pro 关不掉 | `reasoning_effort: "minimal"` |

### DeepSeek V4 特有字段

```json
{
  "model": "deepseek-v4-flash",
  "messages": [...],
  "thinking": {
    "type": "enabled"      // "enabled" | "disabled"
  },
  "reasoning_effort": "high"  // "high" | "max"
}
```

- `thinking.type` 默认 `"enabled"`
- `reasoning_effort` 默认 `"high"`
- 出于兼容：`low`/`medium` 映射→`high`，`xhigh` 映射→`max`
- 不认识的值不报错，静默映射

### Gemini OpenAI 兼容模式

```bash
curl "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions" \
  -H "Authorization: Bearer $GEMINI_API_KEY" \
  -d '{
    "model": "gemini-3-flash-preview",
    "reasoning_effort": "low",
    "messages": [...]
  }'
```

- `reasoning_effort` 与 Gemini 原生 `thinking_level`/`thinking_budget` 互斥
- Gemini 2.5 系列：`reasoning_effort` 映射到 `thinking_budget`（low→1024, medium→8192, high→24576）
- Gemini 3.x 系列：`reasoning_effort` 映射到 `thinking_level`

### OpenAI Reasoning

OpenAI 有两套 API：
- **Chat Completions API**（旧）：`reasoning_effort` 作为顶层参数
- **Responses API**（新）：`reasoning: {effort: "...", summary: "auto"}` 嵌套对象

本包只关注 Chat Completions API（OpenAI 兼容协议的基础）。

## JSON 输出兼容性

| 特性 | OpenAI | DeepSeek | Gemini |
|------|--------|----------|--------|
| `response_format: {type: "json_object"}` | ✅ | ✅ | ✅ |
| `response_format: {type: "json_schema"}` | ✅ | ❌ | 部分支持 |
| 返回 markdown code fence 包裹 | 偶尔 | 常见 | 常见 |
| 返回 bare list（无对象包裹） | 偶尔 | 常见 | 偶尔 |

**必须处理的兼容性问题**：
1. Markdown code fence 剥离（` ```json ... ``` `）
2. Bare list 自动包装到 Pydantic schema 的 list 字段
3. 首尾空白清理

## 流式输出（SSE）

三家格式一致，均为 OpenAI 标准 SSE：

```
data: {"choices": [{"delta": {"content": "..."}}]}
data: [DONE]
```

无兼容性问题。

## 多模态（图片输入）

三家均支持 OpenAI 的 `image_url` 格式：

```json
{
  "type": "image_url",
  "image_url": {"url": "data:image/png;base64,..."}
}
```

无兼容性问题。
