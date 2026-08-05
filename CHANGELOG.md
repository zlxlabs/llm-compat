# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 记录变更。

0.6.1–0.7.3 是本分支上的中间版本号，从未打 tag 发布，因此合并到本次 0.8.0 说明中。
早于 0.7.0 的变更未记录于此，请查阅 git log。当前已发布的 tag 只有 `v0.2.0`、`v0.6.0`，
以及本次的 `v0.8.0`。

## [Unreleased]

### Changed

- **BREAKING**：`detect_provider()` 的返回值从 `str` 改为 `ProviderDetection`；下游需要读取
  `.family` 获取原来的 provider 字符串。

## [0.8.0] - 2026-08-05

### Fixed

- 修复 DeepSeek 思考模式关闭失效（[#7](https://github.com/zlxlabs/llm-compat/issues/7)）：此前
  `_translate` 返回的是 OpenAI SDK 的传参容器形状 `{"extra_body": {"thinking": ...}}`，
  而 llm-compat 通过 httpx 直接发送，不会展开该容器，DeepSeek 因而忽略未知字段，思考实际
  从未关闭；`describe_from_payload` 读取同一位置还会使日志报告错误。现在发送顶层
  `{"thinking": {"type": "disabled"}}`，日志与实际 wire body 使用同一来源。
- 修复 effort clamp 在支持集合内部空档处跳到最高档的问题（[#9](https://github.com/zlxlabs/llm-compat/issues/9)）：
  现在按 `_EFFORT_RANK` 选择最近的支持档位并优先向上取邻；请求低于或高于支持范围时，分别
  钳制到集合内的最低或最高档。
- 移除 DeepSeek 官方未文档化的 `medium` effort（[#8](https://github.com/zlxlabs/llm-compat/issues/8)），
  请求该值现在稳定翻译为 `high`。
- 修复 `validate_config()` 的预检结果与运行时实际翻译结果不一致的问题；两者现在共用
  `providers.resolve_effort_clamp()` 这一单一来源。
- 修复公开 `build_request_payload()` 绕过 reasoning effort 归一化的问题；带空白或大小写变体
  的关闭意图（如 `" none "`、`"NONE"`）现在不会再被误判为最高档。
- 注册包含未排名 effort 的自定义 provider caps 时立即抛出明确异常；此前这类配置会在请求时
  抛出 `KeyError`。即使通过其他路径绕过注册校验，能力解析对未知 effort 也会按 `high` rank
  安全兜底。

### Changed

- **Breaking**：`extra_body` 不再模拟 OpenAI SDK 的展开行为，而是作为普通 wire 字段保留在
  请求体顶层并原样透传。OpenAI SDK 使用它绕过强类型签名，但 llm-compat 直接通过 httpx
  发请求，不存在该约束；Gemini 的 OpenAI 兼容层也可用这个同名顶层字段承载 Google 原生能力。
- **Breaking**：`_FAMILY_CAPABILITIES` / `get_provider_caps()` 的返回结构移除 `min_effort` /
  `max_effort`；`ProviderCaps` 类型已删除。自定义 caps 的 `efforts` 必须全部使用已排名的
  effort 值。
- 思考控制字段收归库管理：经 `**extra` 直接传入的 `thinking` 与 `stream` 会被丢弃并记录
  warning，不能覆盖翻译层按目标 provider 生成的结果。`extra_body` 内部的字段不受此限制，
  因为它们保持嵌套，不会在 content fallback 时污染下一个 provider。
- `providers._deep_merge` 更名为公开的 `deep_merge_payload`。
- content fallback 为每个目标 model 重新翻译思考控制字段，不复用上一个 provider 的结果。

### Migration

- 关闭思考请使用 `reasoning_effort="disabled"`（或别名 `"none"`），不要通过
  `chat(..., thinking={...})` 传入；后者会被丢弃并记录 warning。
- 流式请求使用 `chat_stream()`，不要经 `**extra` 传入 `stream=True`。
- Gemini 用户现在可用 `extra_body={"google": {...}}` 承载 `thinking_config`、`cached_content`
  等原生能力。只传一层即可：Google 官方文档给出的 OpenAI SDK 写法是
  `extra_body={'extra_body': {'google': ...}}`，其中外层是 SDK 传参容器；llm-compat 用户照抄
  会得到错误的 `{"extra_body": {"extra_body": {...}}}`。
- 读取过 `get_provider_caps()["min_effort"]` / `get_provider_caps()["max_effort"]` 的代码，
  需要改用 `resolve_effort_clamp()`。
