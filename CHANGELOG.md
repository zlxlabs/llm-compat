# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 记录变更。

0.6.1、0.6.2、0.6.3 是本分支上的中间版本号，从未打 tag 发布，因此在此处跳过。

## [0.7.0] - 2026-08-04

### Fixed

- 修复 DeepSeek 思考模式关闭失效（[#7](https://github.com/zlxlabs/llm-compat/issues/7)）。此前请求实际发出的是无效的顶层 `{"extra_body": {"thinking": ...}}`，而 `llm-compat` 通过 httpx 直接发送请求，不会展开 OpenAI SDK 的 `extra_body` 容器；同时 `describe_from_payload` 仍会报告思考已禁用，造成日志与实际行为不一致。

### Changed

- **Breaking-ish**：`extra_body` 现在会展开到请求 body 顶层。`model`、`messages`、`stream`、`extra_body`、`thinking` 五个保留键会被丢弃，并记录 warning。
- `providers._deep_merge` 更名为公开的 `deep_merge_payload`。

### Migration

- 需要关闭思考时，请使用 `reasoning_effort="disabled"`（或兼容别名 `"none"`）。不要通过 `extra_body` 传 `thinking`：该字段会被丢弃，并且在 content fallback 切换模型时，可能把 DeepSeek 专用字段带给不认识它的 provider。
