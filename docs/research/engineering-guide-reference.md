# 工程指南参考

本文档是对 `VideoTranscriptAPI/docs/development/llm/engineering_guide.md` 中已验证模式的提炼。
该指南是 llm-compat 包设计的主要参考来源。

完整原文见：`/home/zlx/projects/personal/VideoTranscriptAPI/docs/development/llm/engineering_guide.md`

## 已验证的核心模式

### 1. Provider 检测（fnmatch 模式匹配）

通过模型名识别 provider 族，而非 base_url。这在 New API 代理场景下仍然有效，因为模型名在配置中明确指定。

```python
_DEFAULT_PROVIDER_PATTERNS = (
    ("deepseek-*", "deepseek"),
    ("gpt-*", "openai"),
    ("gemini-*", "gemini"),
    # ...
)
```

**要点**：
- 顺序敏感，具体模式在前
- 支持用户自定义模式覆盖
- 未匹配的模型归入 "generic" 族（透传，不翻译）

### 2. reasoning_effort 三态设计

区分 `null`（默认）、`"disabled"`（关闭）、具体强度值：

| 配置值 | 语义 | 行为 |
|--------|------|------|
| `null` / `None` | 使用 provider 默认 | payload 不加字段 |
| `"disabled"` | 显式关闭思考 | 按 provider 翻译 |
| `"low"` ~ `"max"` | 指定强度 | 按 provider 翻译或透传 |

**关键洞察**：老架构把 "默认" 和 "关闭" 压成同一个 `None`，升级 DeepSeek V4 后 Gemini 2.5 的 "关闭思考" 会静默变为 "默认开启"。

### 3. Provider 能力声明

每个 provider 族声明自己的能力，翻译层据此决策：

```python
_FAMILY_CAPABILITIES = {
    "deepseek": {
        "disable_mode": "thinking_object",   # 用 thinking.type=disabled
        "efforts": {"low", "medium", "high", "max"},
        "effort_mapping": {"low": "high", "medium": "high"},
    },
    "openai": {
        "disable_mode": "effort_none",       # 用 reasoning_effort=none
        "efforts": {"none", "minimal", "low", "medium", "high"},
        "effort_mapping": {},
    },
}
```

### 4. Payload 构建统一入口

所有请求通过 `build_request_payload()` 统一构建，确保：
- 翻译逻辑集中在一个点
- 日志从最终 payload 派生（保证真实）
- 已有的 extra_body 字段通过深合并保留

### 5. 启动时配置校验

项目启动时扫描所有 LLM 配置，打印摘要并 warn 不兼容组合：

```
[LLM] summary: deepseek-v4-flash (deepseek) | thinking=high
[LLM] validator: deepseek-v4-flash (deepseek) | thinking=disabled
[WARN] Provider 'openai_gpt4' does not support reasoning_effort; dropping 'high'
```

### 6. Legacy 兼容

- `"none"` → `"disabled"` + deprecation warn
- 空字符串 / 空白 → `None`
- 未知字符串 → `None` + warn

## 从工程指南到包的演进

工程指南中的以下部分**属于包的职责**：
- §4 Reasoning Effort 配置（providers.py）
- §3 结构化输出 JSON 清洗
- §5 错误处理（重试策略）
- §6 可观测性（请求日志）

以下部分**不属于包的职责**（各项目自行实现）：
- §1 基础架构（配置文件设计）
- §2 Prompt 工程与模板管理
- §4.3 配置文件示例（项目级）
- §4.5 启动日志（项目级，但包提供 `describe_from_payload` 工具函数）

## DeepSeek 模型迁移时间线

⚠️ 旧模型 `deepseek-chat` / `deepseek-reasoner` 将于 **2026-07-24** 停服。

| 旧模型 | 新配置 |
|--------|--------|
| `deepseek-chat` | `deepseek-v4-flash` + `reasoning_effort: "disabled"` |
| `deepseek-reasoner` | `deepseek-v4-flash` + `reasoning_effort: "high"` |

包的 provider 翻译层需要正确处理这两种迁移场景。
