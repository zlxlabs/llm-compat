# DESIGN-note：拒绝与截断的可观测性收口

分诊来源：#24、#25、#26、#27（#13 已按方向 B 关闭）。基线 `7f9e349`（v0.10.0）。

## 目标

消费方不读私有字段、不读响应正文，就能回答三个问题：这次输出**是不是被截断了**、
这次调用**是不是被拒绝了**、**是哪一层判的拒绝**；同时 `client.stats` 与 collector 上报
不再给出与事实矛盾的记录。

## 非目标

- **不改拒绝检测的判定逻辑本身**。正则词表、三层分级、合取门槛是 #22/#23 已定局的边界
  （见 `docs/guides/integration-guide.md#拒绝检测的边界与调优`），本批只搬运判定结果，不重开判定。
- **不做 HTTP shim、不做跨语言运行时**。#13 已定为文档契约方向（`caps.json` + `conformance.json`）。
- **不改 provider caps 表 / 翻译层**。`providers.py` 本批不动。
- 不改 `ContentPolicyError` 已有的 `evidence` / `attempt_layers` 契约（#23 的成果，只做补充不做替换）。

## 方案要点与已否决方案

- **要点**：拆成两个串行增量。

  **增量 1（数据透传，纯字段增量）**：库内部已经算出、随后被丢弃的两类事实，接到公开契约上。
  `refusal.py::_content_and_finish_reason` 已经提取了 `finish_reason` 但只用于拒绝检测；
  `_base.py::_extract_result` 构造 `TokenUsage` 时丢弃了 `usage.completion_tokens_details`。
  这一步不动控制流，只加字段与提取。

  **增量 2（副作用时机，控制流重构）**：把 `stats` 记账与 collector 上报从「拿到响应内容」
  移到「终态确定之后」，并把 `_pending_refusal_report` 从 client 实例字段改为随调用传递。

  两个增量**必须串行**：都要改 `_base.py::_extract_result`（增量 1 在其中加 `finish_reason`
  提取，增量 2 从其中移走 `record_success`），同一函数写入冲突。

- **已否决**：
  - *让消费方继续用 `completion_tokens == max_tokens` 做截断代理判定* —— VideoTranscriptAPI
    生产已证伪：网关把 reasoning token 计入 completion 预算，思考吃掉 88% 后正文被切，
    代理判定既会因 token 计数巧合相等而误判，也会因 provider 不上报 usage 而漏判。
  - *只修 #26 的异常字段、不动 collector 与 trace* —— #23 已经做过这一半（异常挂 evidence）。
    pin 0.8.0 的生产、以及未接 collector 的消费方，失败路径仍然只能靠异常对象，
    `chat_json` 则连异常之外的任何上报都没有。
  - *先扩大 collector 上报覆盖面、把并发串扰（#27）留待后续* —— 在错误的状态载体上扩大
    覆盖面等于放大 bug 触发面，两者必须同卡处理。
  - *两张卡并行派发* —— 见上，写入冲突。

## 关键不变式

1. **截断可判定**：`chat()` / `chat_json()` 成功返回的 `ChatResult.finish_reason` 原样透传
   上游 `choices[0].finish_reason`；上游缺该字段时为 `None`，不伪造。
   代码在 `_base.py::_extract_result`，测试锁死在 `tests/test_types.py` 与 `tests/test_trace_contract.py`。
2. **思考预算可观测**：`TokenUsage.reasoning_tokens` 取自 `usage.completion_tokens_details.reasoning_tokens`，
   缺失时为 `0` 且不影响既有三个 token 字段。代码同上，测试同上。
3. **拒绝依据进账本**：被判拒的每次 attempt，其 `ModelAttempt` 带 `detection_layer` 与
   `finish_reason`，`to_dict()` 可序列化。代码在 `_trace.py::ModelAttempt` 与 `_base.py`
   的 attempt 记录点，测试锁死在 `tests/test_trace_contract.py`。
4. **一次调用一条记录**：任一逻辑调用（`chat` / `chat_json` / `chat_image` / 救援路径 / sync 版）
   在 `stats` 上恰好留下一条记录——成功记 success、失败记 error，不同时记两条。
   代码在各调用点的终态处，测试锁死在新增的记账契约测试（四条路径全覆盖）。
5. **拒绝证据不串调用**：不存在跨调用共享的可变拒绝状态；并发调用各自上报自己的证据。
   代码在 `_base.py` / `client.py` 的证据传递链，测试锁死在并发上报测试（≥5 次连续绿）。
6. **失败路径也上报**：`chat()` 与 `chat_json()` 整链 `ContentPolicyError` 在 raise **之前**
   调用 collector 上报（配了 collector 时）；无 collector 时异常上的 evidence 不丢。
   代码在 `client.py` 的失败分支，测试锁死在失败路径上报测试。
7. **正文不进默认日志**：以上任何新增路径都不把 prompt / response 正文写进 INFO 级日志。
   测试用 caplog 锁死。

## 验收路径

1. **入口**：消费方视角的库公开 API，`LLMClient.chat_json()` / `LLMClient.chat()`，
   对接 mock 的 OpenAI 兼容端点（`tests/` 既有 httpx mock 设施）。
2. **步骤**：
   - 构造上游返回 `finish_reason="length"` + `completion_tokens_details.reasoning_tokens=4431`
     的响应（即 #25 的生产忠实复现），走 `chat()`，读 `result.finish_reason` 与
     `result.usage.reasoning_tokens`。
   - 构造两个模型都被判拒的链，走 `chat_json()`，捕获 `ContentPolicyError`，
     只读公开字段拿到每模型的 layer 与 `finish_reason`；同时断言 collector 收到上报。
   - 无 `content_fallbacks` 配置下走 `chat_json()` 触发 JSON 解析失败，读
     `client.stats.success_count / error_count / total_calls`。
   - 并发发起多个调用（部分被判拒），断言每次上报的 model 与自己的请求一致。
3. **预期**：
   - `finish_reason == "length"`、`reasoning_tokens == 4431`。
   - 异常上每模型 layer 与 finish_reason 可读，collector 收到对应条数上报。
   - 解析失败的调用：`success_count=0, error_count=1, total_calls=1`。
   - 并发上报无错配，连续 5 次全绿。

## review 纪律

本仓风险等级 **personal**，但本批改动核心落在失败路径与记账，按 `CLAUDE.md` 的 infra 例外
走上一档收敛：**连续 2 轮无新增 P1**，review 轮次上限仍为 3。
增量 2 进入 review 循环前须点名一轮**降层审查**：终态写入成功之前已发生哪些不可逆动作
（collector 上报、日志、hook 回调）？守卫用的那个值在并发形态下自身唯一吗？
