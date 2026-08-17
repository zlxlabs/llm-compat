# R2 verdict：mimo-* 反向过匹配与契约锁独立性

verdict: pass

审查对象固定为 `3b498cf4bda9dae6a7ce379c455c789d0f6cb2a5..bf6493a5cd2b64e35e8156d19a7fafb4f98f91f2`，未纳入 R1 提交 `14a791b`。

## 本轮新证据

- OCR 前置实际执行，但 primary、qwen、glm 三腿均在输入阶段失败；缓存证据为 `input_config_error` / 背景文件被解析为失效 `/proc/self/fd/11`，因此不能把 OCR 说成已扫过且干净。本地独立审查继续完成。
- 在 H0 工作树亲自将 `src/llm_compat/providers.py:137` 的 `mimo.json_mode` 单独改为 `json_schema`，未改 snapshot；`uv run pytest -q tests/test_provider_capability_snapshot.py::test_family_capabilities_match_handwritten_contract_snapshot` 以 `family='mimo'` 失败。随后只还原该一行，源码无残留 diff。
- 反向探针命令：`uv run python` 调用 `detect_provider()` / `get_provider_caps()`。`mimo-v2.5-asr`、`mimo-v2.5-tts`、`mimo-v2-pro`、`mimo-v2.5-pro-preview` 均命中 `mimo` 和 `json_object`；`mimo`、`xiaomi-mimo-v2.5` 不命中。仓库没有 ASR/TTS API，只有 `chat`、`chat_json`、`chat_stream`、`chat_image` 等入口（`src/llm_compat/client.py:112-237`）。
- H0 全量回归：`uv run pytest -q`，`1073 passed`；`git diff --check` 通过，最终工作树仅新增本 verdict 文件。

## 反向过匹配审查

### P3-R2-001（接受不修）：`mimo-*` 会覆盖非 chat 名称

- 严重度：P3；违反边界不变式 I1 的“匹配范围应对应可用模型入口”部分，但不违反本 issue 已锁定的单一 `mimo` family 决策。
- 证据：`src/llm_compat/providers.py:40` 使用 `("mimo-*", "mimo")`；反向探针确认 `mimo-v2.5-asr`、`mimo-v2.5-tts`、下线的 `mimo-v2-pro` 也命中。`detect_provider()` 在 `src/llm_compat/providers.py:255-261` 对命中模型直接返回 family，`chat_json()` 在 `src/llm_compat/client.py:154-179` 是唯一会进入结构化 JSON 编排的公开入口之一。
- 分诊：当前包没有 ASR/TTS 入口，且任务卡已明确否决因该现象拆 family；这些名字只在调用方错误地把非 chat 模型送入 `chat_json()` 时才会触发 JSON 能力选择。因此接受为 P3 backlog，不判 P1/P2，也不要求本轮扩大改动。

## 契约锁独立性审查（I7）

- 手写 snapshot 是独立 oracle：`tests/test_provider_capability_snapshot.py:16-18` 禁止 import exporter 或读取 JSON，`tests/test_provider_capability_snapshot.py:19-80` 直接写出各 family 的期望值，`tests/test_provider_capability_snapshot.py:95-103` 与源码表逐值比较。指定红验已证明源码单独漂移会红。
- `caps.json` 的导出一致性锁是“产物写入/同步”锁而非独立语义 oracle：`scripts/export_caps.py:35-47` 从 provider 源表生成，`tests/test_export_caps.py:99-102` 检查 checked-in 字节与重新导出结果一致。它可以保证不漏提交同步产物，但不能单独发现“源码和产物一起被改成错误值”；这正由独立 snapshot 补上。
- `conformance.json` 与 `REVIEWED_VECTORS_DIGEST` 只覆盖 detection/reasoning translation 向量：`scripts/export_conformance.py:121-129` 对 vectors 做 digest，`scripts/export_conformance.py:174-204` 从源码生成并判断 reviewed；`tests/test_conformance.py:70-77` 锁导出同步，`tests/test_conformance.py:120-129` 锁 digest 漂移。它不是 `json_mode` 的 oracle，但这属于契约范围分工，不会让本次 Mimo JSON 能力静默失守。
- 行为锁与表值锁解耦：`tests/test_structured_output.py:69-106` 通过真实 HTTP mock body 断言 Mimo 的 `response_format={"type":"json_object"}` 以及 schema prompt 注入；snapshot 锁的是表值，structured-output 测试锁的是 `chat_json` 行为。全量测试通过，故本轮未发现 P1/P2。

## 降层三问

1. 终态写入成功前的不可逆动作：导出脚本会写入 `caps.json` / `conformance.json`；之后 release tag 或下游按固定 SHA vendoring 后，错误能力表会在消费者仓库中固化，属于不可逆发布边界。本 diff 没有执行发布；snapshot、导出同步锁和行为测试均发生在该边界之前。
2. 守卫值的唯一性：手写 snapshot 以 family 键和值构成单仓唯一的第二来源；`REVIEWED_VECTORS_DIGEST` 是规范化 vectors 集合的 SHA-256 内容地址，同一契约在多消费者中应共享同一 digest，向量增删改会失配。digest 不承担 json_mode 语义审定，不能越权解释为全能力表指纹。
3. 覆盖写入还是行为：`test_checked_in_*_matches_export` 覆盖源码到导出物的写入同步；手写 snapshot 覆盖能力表语义；Mimo structured-output 测试覆盖最终 `chat_json` 行为。三者边界清楚且相互解耦，源码改坏而不改 snapshot 的红验已实际证明不是恒真锁。

## 结论

- P1：0；P2：0；P3：1（上述过匹配，接受不修）。
- 未重复提出 R1 已审过的请求塑形问题，也未把 `thinking.type`、未知模型默认值或 `max_completion_tokens` 列为本轮 finding。
- `verdict: pass`。
