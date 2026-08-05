# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 记录变更。

0.6.1、0.6.2、0.6.3 是本分支上的中间版本号，从未打 tag 发布，因此在此处跳过。

## [0.7.2] - 2026-08-05

### Fixed

- 修复不受支持的 `reasoning_effort` 在 provider family 支持集合内部空洞处无脑跳到最高档的
  问题（[#9](https://github.com/zlxlabs/llm-compat/issues/9)）。现在按 `_EFFORT_RANK` 向上
  取最近邻，边界才钳制到集合最值。
- 移除 DeepSeek 官方未文档化的 `medium` effort；请求该值现在稳定翻译为 `high`，不再透传
  未定义的 provider 行为（[#8](https://github.com/zlxlabs/llm-compat/issues/8)）。

## [0.7.0] - 2026-08-04

### Fixed

- 修复 DeepSeek 思考模式关闭失效（[#7](https://github.com/zlxlabs/llm-compat/issues/7)）。此前请求实际发出的是无效的顶层 `{"extra_body": {"thinking": ...}}`，而 `llm-compat` 通过 httpx 直接发送请求，不会展开 OpenAI SDK 的 `extra_body` 容器；同时 `describe_from_payload` 仍会报告思考已禁用，造成日志与实际行为不一致。

### Changed

- **Breaking-ish**：`extra_body` 现在会展开到请求 body 顶层。`model`、`messages`、`stream`、`extra_body`、`thinking` 五个保留键会被丢弃，并记录 warning。
- `providers._deep_merge` 更名为公开的 `deep_merge_payload`。

### Migration

- 需要关闭思考时，请使用 `reasoning_effort="disabled"`（或兼容别名 `"none"`）。不要通过 `extra_body` 传 `thinking`：该字段会被丢弃，并且在 content fallback 切换模型时，可能把 DeepSeek 专用字段带给不认识它的 provider。

## [0.7.1] - 2026-08-05

### Fixed

- 收口请求参数注入路径：直接经 `**extra` 传入的 `thinking`、`stream`，以及经 `extra_body` 传入的 `thinking`、`reasoning_effort`，都会被丢弃并记录 warning，不能覆盖翻译层按目标 provider 生成的思考控制字段。
- `chat_stream()` 的 `stream=True` 现在由 `_build_payload` 的具名参数显式注入；content fallback 对每个目标 model 重新生成思考字段，日志中的 `describe_from_payload` 与实际 wire body 保持一致。

### Migration

- 思考开关与强度唯一使用具名参数 `reasoning_effort`；流式请求使用 `chat_stream()`。保留字段冲突会被拒绝，不再静默覆盖 provider 翻译结果。
