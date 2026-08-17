# R1 verdict：mimo-* json_object 登记

verdict: pass

审查对象固定为 `3b498cf4bda9dae6a7ce379c455c789d0f6cb2a5..bf6493a5cd2b64e35e8156d19a7fafb4f98f91f2`，未按分支名漂移。风险等级为 `personal`；本 diff 按 provider 能力表的 infra 例外检查 P1（数据丢失、静默出错、崩溃）。

## 结论

冻结 H0 已堵住本卡范围内的静默错误：`mimo-v2.5`、`mimo-v2.5-pro` 及大小写变体命中 `mimo`，`chat_json()` 实际发出的请求体使用 `response_format.type=json_object`，并将 schema 追加到最后一条 user message；不会再因未知模型回落而发出 `type=json_schema`。`qwen-max` 和 `unknown-model-xyz` 仍保持 `openai`、未命中、`json_schema` 的既有行为。

本轮没有可溯源到 spec 不变式的 P1、P2 或 P3 finding；未接受的 P1 数为 0，因此 verdict 为 `pass`。

## 不变式核对

| 不变式 | 结果 | 证据 |
|---|---|---|
| I1 | pass | `src/llm_compat/providers.py:40` 登记 `mimo-*`；`tests/test_providers.py:385-404` 覆盖两个模型、大小写和无 warning。H0 临时环境探针返回 `mimo/matched`。 |
| I2 | pass | `src/llm_compat/providers.py:133-138` 设置 `json_mode=json_object`；`caps.json:217` 起及手写 snapshot `tests/test_provider_capability_snapshot.py:74-80` 一致。 |
| I3 | pass | 现有请求塑形路径 `src/llm_compat/_base.py:892-917` 在 json_object 分支注入 schema；新增 `tests/test_structured_output.py:69-103` 锁定 MiMo 的 Pydantic/显式 schema 两条请求路径。真实 HTTP mock 探针捕获到 `response_format={"type":"json_object"}` 和最后 user message 的 schema。 |
| I4 | pass | H0 临时环境直接调用 `_build_json_payload()`：`qwen-max`、`unknown-model-xyz` 均为 `family=openai, matched=false, mode=json_schema`；新增 `mimo-*` 没有改变未知模型回落。 |
| I5 | pass | MiMo caps 的 `disable_mode=na` 位于 `src/llm_compat/providers.py:133-137`；`tests/test_providers.py:777-779` 覆盖 disabled 不发字段、high 透传、minimal clamp。 |
| I6 | pass | `src/llm_compat/providers.py:134-136` 为 `efforts={low,medium,high}`、`supports_vision=False`；对应 snapshot 与 provider matrix 均通过。 |
| I7 | pass | `caps.json:137-138,217` 含顺序正确的 pattern 和 family；`conformance.json:5,4914` 为 `reviewed=true` 且含 MiMo 向量；`scripts/export_conformance.py:34,184` 纳入模型并以更新 digest 计算 reviewed。H0 运行 `export_caps.py`、`export_conformance.py` 后分别与 checked-in 文件 `cmp` 字节一致。 |
| I8 | pass | 冻结 diff 只在 `providers.py` 增加 pattern/family；`_DEFAULT_CAPS`、`_PARTIAL_CAPS_DEFAULTS`、`_translate` 决策树和 `_base.py` 均未改动。证据：`git diff base H0 -- src/llm_compat/_base.py` 为空。 |

## 降层三问（infra）

1. 终态写入成功之前的不可逆动作：能力判断、schema 注入和 payload 合并都发生在内存中；真正不可逆的动作是 `src/llm_compat/client.py:49-59` 的 HTTP POST，序列化后的 `response_format` 一旦发出无法撤回。H0 的 `src/llm_compat/_base.py:892-923` 在该动作前已确定 MiMo 的 json_object 请求体；实测发出的 body 正确，因此没有观察到“错误格式已发出”的 P1 路径。
2. 守卫值的唯一性：在默认 pattern 表内，`mimo-*` 与现有 `deepseek-*`、`gemini-*`、`gpt-*`、`doubao-*`、`o*` 前缀不重叠，first-match 结果确定。模型名不是跨所有 New API 部署和任意自定义 alias 的全球唯一标识；自定义 pattern 按 `src/llm_compat/providers.py:238-243` 置于默认表之前是既有、显式的覆盖机制。故本轮只能确认“当前默认表 + issue 指定模型名”唯一，不能把它扩大解释为所有网关 alias 的唯一性；这不构成本 diff 的 P1。
3. 保护覆盖的是行为而非仅写入：`caps.json`/snapshot/exporter 检查知识与导出一致性，但运行时 `get_provider_caps()` 的 `json_mode` 被 `_build_json_payload()` 消费，决定真实 wire payload，并在 json_object 模式注入 schema。上述 HTTP mock 已验证行为层，而不只是文件值层。

## 验证记录

- H0 临时目录运行：`uv run --frozen pytest tests/test_providers.py tests/test_structured_output.py tests/test_export_caps.py tests/test_conformance.py tests/test_provider_capability_snapshot.py tests/test_compat.py -q`，结果 `718 passed`。
- 真实 payload 探针：`mimo-v2.5-pro` 发 `{"type":"json_object"}`，最后一条 user message 含 `Respond with valid JSON matching this schema`；`qwen-max` 发 `{"type":"json_schema", ...}`。
- 生成物一致性：H0 exporter 输出与 `caps.json`、`conformance.json` 均 `cmp` 相同；conformance 中 MiMo 向量数为 30，`reviewed=true`。
- 官方 MiMo 结构化输出文档列出支持模型为 `mimo-v2.5-pro`、`mimo-v2.5`，并要求 `response_format={"type":"json_object"}`：<https://mimo.mi.com/docs/zh-CN/quick-start/usage-guide/text-generation/structured-output>。

## 明确不纳入本轮

未登记的其他未知模型、未知模型默认 `json_schema`、`max_completion_tokens` 默认值、官方 `thinking.type` 翻译、vision=False 和 disable_mode=na 均按任务卡列明的边界处理，不作为 finding。
