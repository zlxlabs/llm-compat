<!-- delegate-outcome: succeeded -->
## 审查结论

审查对象：`0aa18e5..825d732`（H0 冻结），分支 `card/refusal-evidence`。

风险等级：`personal`。本改动核心涉及拒绝分类、fallback 和失败路径，适用 infra/状态机例外；收敛条件为连续 2 轮无新增 P1。本轮是 R1。

总体 verdict：`fail`。发现 1 条未修 P1；另外有 2 条 P2、3 条 P3/backlog。因 P1 尚未修复，本轮不能判 pass。

本轮新证据：OCR 前置扫描返回 `status=reviewed`、`coverage=complete`（MiniMax-M3，8 条候选，5 条确认、2 条反驳、1 条未验证）；随后实际阅读冻结 diff 的全部 21 个文件、执行运行时探针、复跑三条验收命令，并对长度门槛、内置句式位置门槛、调用方词条位置门槛各做了一次“改坏→测试→精确还原”的变异验证。以下结论只针对 H0 范围。

## Findings

### F1 — P1：严格文本模式仍会把普通澄清式道歉静默判成拒绝

- 级别：P1。
- 位置：`src/llm_compat/refusal.py:75-76`；实际进入 fallback/救援的路径为 `src/llm_compat/_base.py:733-798`。
- 溯源：P1 红线“静默出错（结果错但不报错）”；同时违背已锁定的“strict 句式 + 长度/位置门槛”决策。长度和位置门槛本身虽满足，但 `cn_apology_meta` 不是足够严格的拒绝句式。
- 复现场景：

  ```text
  response = {
    "choices": [{
      "message": {"content": "抱歉，我需要先了解一下你的具体需求"},
      "finish_reason": "stop"
    }]
  }
  ```

  `detect_refusal(response)` 返回 `is_refusal=True`、`layer=text_pattern`、`signal=pattern:cn_apology_meta`、`matched_text='抱歉，我'`、`match_position=0`。使用 `content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]}`，并让两个模型都返回同类正常澄清，客户端不会抛错，而是返回该澄清文本并标记 `refusal_suspected=True`、终态 `content_policy_recovered`。这会丢弃一个实际可用的正常回答，并把错误分类暴露给下游。
- 实际输出摘要：`false-positive-client 抱歉，我需要先了解一下你的具体需求 True content_policy_recovered`；日志为 `signal=pattern:cn_apology_meta`。
- 修复建议：收紧 `cn_apology_meta`，要求道歉后在有限窗口内出现明确拒绝 cue（如“无法/不能/没办法/不提供/拒绝”），并增加“抱歉，我需要先确认/了解需求”这类非拒绝开场的回归用例。保留现有长度和位置门槛。

### F2 — P2：JSON 救援校验失败前已写入 success 统计，最终抛错却没有 error 统计

- 级别：P2。
- 位置：`src/llm_compat/_base.py:601-612`，以及被救援调用的 `src/llm_compat/_base.py:332-334`。
- 溯源：审查要求“静默出错专项”中的“统计口径记错”；并涉及不变式 6（救援候选必须重新清洗/校验，失败转 `ContentPolicyError`，不能被当作成功）。
- 复现场景：两个模型分别返回 `"我无法回答该问题"` 与 `"I cannot assist"`，调用 `chat_json(..., schema=None)`。救援调用 `_extract_result()` 时先执行 `stats.record_success()`，之后 `parse_candidate()` 才因 JSON 解析失败而转为 `ContentPolicyError`。
- 实际输出：`json-rescue-failure ... best candidate rescue failed: Failed to parse JSON ... success_count= 1 error_count= 0 total_calls= 1`。因此调用方收到异常，但统计把失败请求记成成功，`success_rate` 也会被抬高。
- 修复建议：让救援路径在 JSON 清洗和 schema 校验成功后再记录 success；校验失败时记录与最终终态一致的 error，或对救援路径精确回滚这次 success 记账。不要吞掉 `ContentPolicyError`；现有错误传播行为应保留。

### F3 — P2：HTTP 内容审查错误路径没有按新契约写 WARNING，且无 fallback 时错误消息没有模型 layer

- 级别：P2。
- 位置：`src/llm_compat/_base.py:687-731`。
- 溯源：不变式 8（判定为拒绝时 WARNING 必须含 model/layer/signal/片段/位置/正文长度/finish_reason，`ContentPolicyError` 消息带每个被拒模型的 layer）。
- 复现场景 A：让 `_request()` 对每个模型抛出 HTTP 400、body 为 `{"error":{"message":"content_policy_violation"}}`，配置两个 fallback 模型。最终异常的 `attempt_layers` 虽为 `{'deepseek-v4': 'http_error', 'gpt-4.1-mini': 'http_error'}`，但运行期间没有 `Refusal detected` 或等价 WARNING。
- 复现场景 B：同一 HTTP 内容审查错误不配置 fallback（或配置不匹配），`has_fallback=False`，错误直接穿过 `inner.send(response)`；抛出的 `ContentPolicyError` 没有 `attempt_layers`，消息也不含 `model=http_error`。
- 实际输出 A：`http-error All models refused: ['deepseek-v4', 'gpt-4.1-mini'] (deepseek-v4=http_error, gpt-4.1-mini=http_error) {'deepseek-v4': 'http_error', 'gpt-4.1-mini': 'http_error'} None`，无其他 WARNING。
- 修复建议：HTTP 内容审查被归类时补一条结构化 WARNING（正文不存在时明确记录空片段、位置 -1、长度 0、finish_reason None），并统一单模型/无 fallback 路径的 `ContentPolicyError` 包装，使消息和 `attempt_layers` 至少包含当前模型的 `http_error`。

## 降层三问

### ① 终态写入成功之前的不可逆动作

拒绝响应进入 `src/llm_compat/_base.py:759-798` 时，代码先关闭当前 generator（`inner.close()`），然后把正文快照、证据、layer 和候选放入本次 orchestrator 的内存账本，并设置 `_pending_refusal_report`；外层随后在 `:827` 记录 fallback/refusal 统计。generator 关闭和上游响应已消费不可逆，但正文仍暂存在 `refusal_candidates`/`last_content` 中。Collector 上报不在 `trace.freeze()` 之前发生：`LLMClient.chat()` 成功返回后才在 `client.py:128-151` 上报；`chat_json()` 仍没有调用该上报函数，这是 H0 之前的存量路径。

救援中 `_extract_result()` 在 JSON/schema 校验之前写 success 统计（F2），这是终态成功尚未确认就发生的外部可见副作用。校验失败后没有回滚，因此保护并非事务性的。

### ② 守卫值在实际调用形态下是否可靠

对于本次支持的字符串响应，`RefusalEvidence` 是 frozen dataclass，`is_inferred` 由 layer 派生，候选筛选用它判断“只救推断层”，可靠；长度/位置证据也由同一次 `_text_evidence()` 计算。`attempt_layers` 对文本/结构化/畸形响应由同一证据写入，对 HTTP 错误则手工写入 `http_error`，所以 HTTP 路径不是 `RefusalLayer` 的同一枚举来源，且在无 fallback 分支没有写入错误对象（F3）。

候选的 `content` 在缓存时被归一为字符串；这与 `ChatResult.content: str` 的公开契约一致，但会排除不符合该契约的非字符串上游 payload。对正常字符串调用形态，`candidate["content"]` 的非空守卫可靠；对 JSON 救援来说，真正不可靠的是“已调用 `_extract_result` 就等于成功”，因为校验仍在其后（F2）。

### ③ 保护覆盖的是“写入”还是“行为”

`rescue_best_candidate()` 保护了行为：只从 `is_inferred` 且正文非空的候选中选最长者，成功后写入 `refusal_suspected`、`refusal_evidence` 和 `content_policy_recovered`。它也会对 `chat_json()` 执行候选解析，失败转错误，未把失败吞成成功。

但它没有保护 success 统计写入：`_extract_result()` 的写入早于 parse/schema 校验，救援失败后没有撤销（F2）。HTTP 错误路径也只保护了终态 layer 汇总写入，没有覆盖 WARNING 和无 fallback 错误包装行为（F3）。

## 熵增审查

- `RefusalEvidence`：不是熵 +1。生产者是 `detect_refusal()`；第二消费者及以上包括 BaseClient 的候选筛选/救援、`ChatResult`、`ContentPolicyError`、Collector payload、日志和公开 `to_dict()`。它承载本次设计要求的跨边界证据。
- `RefusalPolicy`：不是熵 +1。直接调用方是 `detect_refusal()`，客户端调用方是 `_content_fallback_orchestrator()`；它把长度、位置、关键词模式和词条作为同一策略传递，避免两套门槛实现。
- `RefusalLayer`：不是熵 +1。它约束 `RefusalEvidence.layer`，并被 `is_inferred`、日志、候选救援和错误 layer 汇总共同消费。`custom_override` 表示 detector 明确否决文本层，`none` 表示没有拒绝信号；二者是公开证据解释的一部分，不是额外 fallback 分支。
- `refusal_keywords_mode`：必要的公开配置。客户端保存它并在构造 `RefusalPolicy` 时消费；同步客户端继承同一构造契约，`extend/replace` 还由直接检测测试和 URL 词表测试共同锁定。
- `refusal_max_content_length`：必要的公开配置。客户端消费它构造策略，内置 pattern 与调用方词条两条文本路径共同受其约束。
- `refusal_head_window`：必要的公开配置。客户端消费它构造策略，内置 pattern 与调用方词条两条文本路径共同受其约束。
- `on_all_refused`：必要的公开配置。构造函数保存它，`rescue_best_candidate()` 消费它决定救援/抛错；同步客户端复用同一行为。它没有引入另一套默认策略。
- `ChatResult.refusal_suspected`：不是熵 +1。救援路径写入，调用方/README/指南读取；它是默认行为变更后避免下游把可疑正文当作普通成功的公开标志。
- `ChatResult.refusal_evidence`：不是熵 +1。救援路径写入，调用方、Collector 记录和文档读取；它与 boolean 标志承担不同信息量，不能由后者替代。
- `ContentPolicyError.evidence`：不是熵 +1。终态错误写入，调用方和 Collector/诊断消费；它保留最后拒绝证据。
- `ContentPolicyError.attempt_layers`：不是熵 +1。终态错误写入，消息摘要、调用方和测试消费；它解决多模型链中“哪个模型以哪一层拒绝”的可见性问题。
- `refusal_candidates`：必要的内部状态，生产路径逐次追加，救援路径跨候选取最长者消费；没有第二个无关抽象消费者，但这是实现“链耗尽后选最长候选”所需的最小账本。
- `attempt_layers`：不是熵 +1。它同时用于 `refusal_summary()` 和 `ContentPolicyError.attempt_layers`，两个下游消费者明确存在。
- `rescue_failure`：必要的最小状态，异常路径写入、终态错误消息消费；它防止 JSON 救援失败被泛化成无原因成功或普通拒绝。
- `parse_candidate`：单一 JSON 消费者，但仍必要。它是 `_content_fallback_orchestrator()` 的最小回调边界，唯一用途就是满足不变式 6 对 `chat_json()` 候选重新清洗/schema 校验的要求，不应继续泛化成新框架。
- `_log_refusal()`：不是熵 +1。结构化、custom detector、文本三条拒绝路径共同调用，避免日志字段漂移。

## 变异验证

变异前工作树干净；每次只改一处生产行，命令结束后用 `apply_patch` 精确恢复同一行；第三次恢复后 `git diff -- src/llm_compat/refusal.py` 为空。

1. 改坏全文长度门槛：将 `if not content or content_length > policy.max_content_length:` 改为 `if not content:`。命令：`uv run pytest tests/test_refusal.py -q`。原始输出摘要：

   ```text
   ..............................FF..F.................. [100%]
   FAILED ...test_length_gate_rejects_long_builtin_match_at_head
   FAILED ...test_length_gate_rejects_long_extra_keyword_at_head
   FAILED ...test_length_gate_boundary_is_inclusive[301-False]
   3 failed, 50 passed in 0.11s
   ```

2. 改坏内置 pattern 位置门槛：将 `if match is not None and match.start() < policy.head_window:` 改为 `if match is not None:`。命令：`uv run pytest tests/test_refusal.py -q -k 'position_and_length_are_conjunctive_gates or length_gate_boundary'`。原始输出：

   ```text
   .F... [100%]
   FAILED ...test_position_and_length_are_conjunctive_gates[...-False]
   AssertionError: assert True is False
   1 failed, 4 passed, 48 deselected in 0.08s
   ```

3. 改坏调用方词条位置门槛：将 `if position != -1 and position < policy.head_window:` 改为 `if position != -1:`。命令：`uv run pytest tests/test_refusal.py -q -k extra_keyword_position_gate_within_length_limit`。原始输出：

   ```text
   F [100%]
   FAILED ...test_extra_keyword_position_gate_within_length_limit
   AssertionError: assert True is False
   1 failed, 52 deselected in 0.08s
   ```

这些结果证明三条门槛测试不是恒真断言。另有一处测试注释（`tests/test_refusal.py:135-136`）把长 custom keyword 用例说成独立锁定位置门槛；实际长度为 5000+，在 `refusal.py:213` 先被长度门槛短路。位置门槛由第 3 个变异对应的独立测试锁定，故该注释是 P3 backlog，不影响当前测试约束力。

## 验证命令

最终现场（所有变异已还原后）执行：

```text
$ uv run pytest tests/ -q
1119 passed in 2.35s

$ uv run ruff check src/ tests/
All checks passed!

$ uv run mypy src/
Success: no issues found in 17 source files

$ git diff --check && git status --short --branch
## card/refusal-evidence...origin/card/refusal-evidence
```

OCR 前置：`ocr-review --repo ... --from 0aa18e5 --to 825d732 ...` 返回 `status=reviewed`、`cli_status=complete`、`coverage=complete`；不是 skipped。OCR 报告的统计双计数意见经核对不成立：`record_fallback()` 只增加 fallback/refusal 独立维度，测试也锁定救援成功时 `success_count=1,total_calls=1`。OCR 提到的非字符串候选筛选不纳入本轮 finding：公开 `ChatResult.content` 是 `str`，普通 provider 响应契约为字符串；其余 OCR 低级长度计数意见已被实际字符计数反驳。

## 契约一致性

- `pyproject.toml:3`、`CHANGELOG.md:10-28` 和 `uv.lock:221-223` 均同步到 `0.10.0`，发布契约一致。
- README 的 fallback/evidence 示例与 `ChatResult` 字段一致；integration guide 对 `extend/replace`、长度/位置门槛、默认 `return_best` 和 JSON rescue 的主行为与代码一致。
- `docs/guides/integration-guide.md:201-202` 只写了 `finish_reason=content_filter/safety`，遗漏代码和设计规格支持的 `content_policy`。这是文档遗漏，接受为 P3 backlog，不据此改变总体 verdict。
- guide 声称每次拒绝写 WARNING；HTTP 内容政策错误路径实际不满足，已列 F3。Collector 的 `chat_json()` 遗留上报缺口在 H0 前已存在，本轮不计 finding。

## Backlog（存量或接受不修的 P2/P3）

- P3，接受不修：`tests/test_client_fallback.py:52-82` 的 warning 测试只验证存在 WARNING 和 result evidence，没有断言日志文本包含 model/layer/signal/片段/位置/正文长度/finish_reason；F3 的生产路径遗漏仍通过独立探针发现。本轮不因测试增强缺口再开修复卡。
- P3，接受不修：`tests/test_refusal.py:135-136` 的注释错误地描述长度门槛用例；独立位置测试已经有真实约束力，改注释不影响运行行为。
- P3，接受不修：integration guide 漏写 `finish_reason=content_policy`，代码和设计规格正确，属于文档补全。
- 存量问题，不计本轮：base commit 已存在 `choices=[{}]` 这类缺失 message/finish_reason 的 malformed payload 可能继续落到 `_extract_result()` 的 KeyError/空内容路径；该行为在 `0aa18e5` 已存在，需另开 malformed-contract 卡，不能借本轮 diff 追责。
- 存量问题，不计本轮：`chat_json()` 不调用 `_maybe_report_refusal()`，可能留下 pending collector report；该调用缺口在 `0aa18e5` 已存在，不能作为本轮 finding。

## 固定条款

执行器必须在本卡分支上小步 commit，未提交的工作按未完成处理。本卡产物是单个 verdict 文件，允许 1 次提交，但必须在写完 verdict 后立即提交，不得把提交留给验收方。若做了变异验证，先确认工作树已还原干净再提交。

本卡不新增任何生产代码与抽象。

执行器自声明 outcome（与 review 的 pass/fail verdict 正交）：本次审查工作已完成，故为 `succeeded`。
