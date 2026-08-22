# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 记录变更。

0.6.1–0.7.3 是本分支上的中间版本号，从未打 tag 发布，因此合并到 0.8.0 说明中。
早于 0.7.0 的变更未记录于此，请查阅 git log。当前已发布的 tag 为 `v0.2.0`、`v0.6.0`、
`v0.8.0`、`v0.9.0`，以及本次的 `v0.9.1`。

## [Unreleased]

- 收紧拒绝句式表对中英混排空白和“不确定后继续作答”的处理，减少文本层假阳性并覆盖常见
  `作为 AI` 表达。
- 在集成指南中明确文本层是偏向漏判的启发式，记录 `refusal_max_content_length=0`、
  `refusal_keywords_mode="replace"` 和 `refusal_detector` 返回 `False` 三条现有逃生门，以及后续
  个案走配置或 backlog 的处置约定。

## [0.10.0] - 2026-08-22

### Changed

- **Breaking**：拒绝检测默认词表从普通题材词改为严格的拒绝言语行为正则；文本判定同时要求
  句式命中位于正文前 120 字且全文不超过 300 字，降低正常长文被误判的风险。
- 拒绝句式统一为“第一人称 + 情态否定 + 任务动词，后接转折时视为继续作答”的主规则，英文政策归因保留独立精确规则；
  普通澄清、补充回答和转述他人观点不会仅因出现否定词而触发。
- 拒绝判定现在返回可序列化的 `RefusalEvidence`，区分 provider 声明层、畸形响应、文本推断层
  和调用方 detector；结构化声明优先，`refusal_detector` 返回 `False` 可否决文本层判定。
- **Breaking**：链耗尽时 `on_all_refused` 默认改为 `"return_best"`。若所有丢弃决策都来自推断层，
  返回最长候选并设置 `ChatResult.refusal_suspected=True`；声明层拒绝仍抛出 `ContentPolicyError`。

### Migration

- 需要保留旧的“所有候选拒绝即抛错”行为时显式设置 `on_all_refused="raise"`。
- 需要接管文本词表时设置 `refusal_keywords_mode="replace"`；`refusal_keywords` 和
  `refusal_keywords_url` 仍按子串匹配，但继续受长度/位置门槛约束。
- 处理可能被文本推断层救援的结果时检查 `result.refusal_suspected` 和
  `result.refusal_evidence`，并对 `chat_json()` 继续使用 `parsed` 做结构校验。

## [0.9.1] - 2026-08-17

### Added

- 登记小米 MiMo `mimo-*` provider family。`chat_json()` 现在为 `mimo-v2.5-pro` 和
  `mimo-v2.5` 使用 `json_object` 并将 Pydantic 或显式 JSON Schema 注入最后一条 user
  message，避免未知模型回落到 `json_schema` 导致网关静默截断 JSON。该 family 保守取
  `disable_mode=na`、`supports_vision=False`，reasoning effort 支持 `low`、`medium`、`high`。

## [0.9.0] - 2026-08-06

### Added

- 新增跨语言共享产物 `caps.json`（provider 能力知识）和 `conformance.json`（其中 `vectors` 当前为 360 条，
  数量随产物更新；以 JSON 的 `vectors` 数组为准）的参数翻译行为向量；Bun/JS、Go 等下游
  可按[跨语言能力与契约指南](docs/guides/cross-language-caps.md)
  读取 family 能力、实现参数翻译并运行向量自证。`conformance.json` 只有在 `reviewed=true`
  时才可作为人工审定过的契约使用。family、pattern 和 warning 类别的清单与数量分别以
  `caps.json` 的 `families`、`patterns` 和 `conformance.json` 的 `warning_categories` 字段为准，
  随产物更新。
- 新增 `strict_unknown_models` client 构造参数，默认值为 `False`，不改变任何现有行为。
  开启后，未匹配任何已知 provider family 的模型会丢弃全部 reasoning 参数、将 JSON 模式降级为
  `json_object`，并从 vision fallback 链中移除。
- 可通过 `detect_provider("your-model").matched` 判断模型是否匹配到已知 provider family。
- 公开函数新增 `strict` 关键字参数：
  `providers.build_request_payload`、`_compat.validate_config`、
  `_compat.validate_fallback_config`、`fallback.filter_by_modality`。

### Changed

- 明确 `chat_json()` 中 `json_schema` 与 `schema` 的职责分工：`json_schema` 用于 API 请求侧的
  `response_format`，`schema` 用于返回值反序列化与校验。只传 `json_schema` 时不会做返回值结构校验，
  库会打一条 warning；需要校验请传入 `schema=<PydanticModel>`。
- **Breaking**：`register_provider(..., caps=...)` 现在会在注册时按完整 caps schema 校验能力记录。
  以前只给 `efforts` 的调用会从运行时崩溃改为立即抛出 `ValueError`，例如
  `register_provider("acme-*", "acme", caps={"efforts": frozenset({"low"})})` 会因缺少
  `disable_mode`、`supports_vision` 和 `json_mode` 报错。正确写法是提供 `caps.json` 的
  `schema.required_keys` 中列出的必需键：
  `register_provider("acme-*", "acme", caps={"disable_mode": "na", "efforts": frozenset({"low"}),
  "supports_vision": True, "json_mode": "json_object"})`。`disable_mode` 与 `json_mode` 的合法
  枚举值、`schema.required_keys` 中的必需键、字段类型以及 `efforts` 的允许值，请查阅 `caps.json` 的 `enums` 与
  `schema` 节（`efforts` 的允许值是 `effort_rank` 的键）。
- `register_provider(..., caps=...)` 现在保存调用方 caps dict 的规范化稳定快照。注册后再修改原
  dict 或 `efforts` 的 set/list/tuple 不会热更新能力表，且不会报错；请构造新的 caps dict，
  再次调用 `register_provider()`。`efforts` 会统一保存为不可变的 `frozenset`。
- **BREAKING**：`detect_provider()` 的返回值从 `str` 改为 `ProviderDetection`。迁移时请逐项检查：
  - `detect_provider(m) == "deepseek"` → `detect_provider(m).family == "deepseek"`。
  - `detect_provider(m) in {"deepseek", "openai"}` 或把结果作为 dict key → 先取
    `detect_provider(m).family`。
  - `json.dumps(detect_provider(m))` → 会抛 `TypeError`；改为
    `json.dumps(detect_provider(m).family)`。
  - f-string、`str()` 或日志格式化 → 现在会输出
    `ProviderDetection(family=..., matched=...)` 而不是族名；改为格式化
    `detect_provider(m).family`。
  - `sorted()` 或直接比较排序 → `ProviderDetection` 未定义序关系；改为使用
    `.family`，或显式提供 `key=lambda detection: detection.family`。
  - pickle 或跨进程传递 → 返回类型已变化；需要保持字符串协议时，先取并传递
    `.family`。
  - `get_provider_caps(...)` 返回的是副本，就地覆盖其键值会静默失效；需要修改自定义能力时，
    改用 `register_provider(..., caps=...)`。
  - 新增能力：`.matched` 可区分真正匹配到已知 family 与未知模型兜底到 `openai` family。

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
