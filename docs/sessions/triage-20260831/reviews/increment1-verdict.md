# 增量 1 独立审查 Verdict

## 结论

**通过；本轮 0 条 finding。** 固定审查对象为 `7f9e349..1720056`（PR #29），未纳入审查期间远端 `main` 后续提交。风险等级为 `personal`；没有发现数据丢失、静默出错或崩溃类缺陷。

本轮新证据为：固定 SHA 的逐行 diff 与 H0 源码、共享编排器的调用链核查、H0 全量测试/类型检查/Ruff 输出、base 临时工作树红验，以及对缺失值和非整数值的直接边界探针。审查期间远端 `main` 从 `7f9e349` 前进到 `a31839c`，但审查对象仍冻结为本文件开头的 SHA 范围。

任务卡指定的 `docs/sessions/triage-20260831/design.md` 在 base `7f9e349` 和 H0 `1720056` 的仓库树中均不存在；因此本轮以任务卡列出的七条不变式作为审查契约，并将该文档缺失作为过程限制记录，不据此提出 finding。

## 不变式逐条核验

1. **`finish_reason` 原样透传：通过。** H0 的 `src/llm_compat/_base.py:313-361` 从 `data["choices"][0]` 取 `choice.get("finish_reason")`，没有归一化、映射或枚举校验；缺字段自然得到 `None`。`tests/test_finish_reason_passthrough.py:61-80` 锁住 `"length"` 和缺字段的 `None`，且直接断言不是空串或 `"stop"`。

2. **`reasoning_tokens` 缺失/非整数为 0，旧计数不变：通过。** `src/llm_compat/_base.py:327-339` 仅在 `completion_tokens_details` 为字典且 `reasoning_tokens` 为非布尔整数时取值，否则保持 0；`prompt_tokens`、`completion_tokens`、`total_tokens` 仍从原字段读取。`tests/test_finish_reason_passthrough.py:83-91` 锁住 details 缺失，`tests/test_types.py:22-35` 锁住默认值及旧字段不变。另以 H0 直接探针覆盖 `{}`、缺键、`None`、字符串、布尔值和整数，实际结果为 `[0, 0, 0, 0, 17]`，退出成功。

3. **被判拒 attempt 带实际判定层和该次上游 finish_reason：通过。** `src/llm_compat/_base.py:711-725` 为 HTTP 内容策略错误记录 `http_error`；`:756-795` 对响应拒绝记录 `evidence.layer` 和 `choice.get("finish_reason")`。文本层、结构化层、HTTP 层分别由 `tests/test_finish_reason_passthrough.py:137-215` 锁住；`to_dict()` 和 `json.dumps()` 也在这些测试中验证。

4. **正常 attempt 新字段为 None，既有序列化契约不变：通过。** 正常响应路径 `src/llm_compat/_base.py:834-842` 使用新字段默认值，`:779-795` 也仅在 `response_is_refusal` 时填入新字段。`ModelAttempt.to_dict()` 仍为 `asdict(self)`（`src/llm_compat/_trace.py:39-40`），没有改名或重解释既有字段；新测试 `tests/test_finish_reason_passthrough.py:218-233` 和 `tests/test_trace_contract.py:98-142` 验证正常值、既有 key、两个新 key 及 JSON 可序列化。

5. **默认值与构造兼容：通过。** `TokenUsage.reasoning_tokens` 位于 `src/llm_compat/_types.py:15` 末尾，`ChatResult.finish_reason` 位于 `:32` 末尾；`ModelAttempt` 两个字段位于 `src/llm_compat/_trace.py:36-37` 末尾，builder 参数和转发位于 `:100-118`。旧构造点无需传新参数；`tests/test_types.py:22-35` 与 `tests/test_trace_contract.py:98-142` 锁住默认值和构造行为。

6. **默认 INFO 日志不新增正文泄露：通过。** 新增解析逻辑只在 `src/llm_compat/_base.py:341-345` 记录延迟和 token 数，不记录 prompt、messages 或响应正文；拒绝 attempt 的新 trace 字段也没有新增 INFO 日志。`tests/test_trace_contract.py:24-51` 继续验证序列化结果不含 prompt、messages、payload、headers、API key 或 raw content。

7. **async/sync 行为一致：通过。** H0 的 `src/llm_compat/client.py:91-129` 与 `src/llm_compat/sync.py:76-113` 分别驱动同一个 `BaseClient._chat_orchestrator`/`_json_chat_orchestrator`，而数据提取只在共享的 `src/llm_compat/_base.py:313-361`。新增 `tests/test_finish_reason_passthrough.py:108-134` 实测 sync 的 chat/chat_json；既有 `tests/test_trace_orchestration.py:50-67` 还比较了 async/sync trace 语义。

## 重点检查

- `ModelAttempt.to_dict()` 的既有 key 仍由同一 `asdict` 生成；diff 只在末尾新增 `detection_layer` 与 `finish_reason`，没有删除、改名或改变既有字段语义。
- `_extract_result` 的三个业务调用点均接入同一提取逻辑：simple 路径 `src/llm_compat/_base.py:424-427`，JSON 路径 `:481-484`，救援最佳候选路径 `:618-624`，没有发现任一路径静默丢 `finish_reason` 或 `reasoning_tokens`。
- 熵增审查：本次 diff 没有新增抽象、接口、状态、包装层或配置项；只增加公开字段、局部解析变量和对应测试/文档，不存在单实现转发层或状态镜像。

## 红验留痕

在 base SHA `7f9e349` 的独立临时 worktree `/tmp/llm-compat-review-base.9f5PmD` 中，仅拷入 `tests/test_finish_reason_passthrough.py`；`git status --short` 仅显示该一个未跟踪测试文件。拷入后先执行：

- `grep -n '^async def test_chat_exposes_length_finish_reason_and_reasoning_tokens\|^async def test_refused_attempt_records_detection_layer_and_finish_reason' tests/test_finish_reason_passthrough.py`，命中第 61、137 行，确认目标测试在文件中。
- `uv run pytest --collect-only -q tests/test_finish_reason_passthrough.py`，显示 9 个测试节点，退出码 0，确认确实被收集。
- `uv run pytest -q tests/test_finish_reason_passthrough.py::test_chat_exposes_length_finish_reason_and_reasoning_tokens tests/test_finish_reason_passthrough.py::test_refused_attempt_records_detection_layer_and_finish_reason`，2 failed，退出码 1：分别为 `ChatResult` 没有 `finish_reason`、`ModelAttempt` 没有 `detection_layer`。因此两条抽查测试在 base 上确实红，不是未注入或未收集。

首次尝试红验时曾误在主工作树运行 collection，因路径错误得到“file or directory not found”；该结果不计入红验，随后按上述正确 base worktree 重跑并得到有效红验结果。

## 命令验证

以下均针对 H0 `1720056` 的独立临时 worktree `/tmp/llm-compat-review-h0.aN6nkz`：

- `uv run pytest`：1222 passed，退出码 **0**。
- `uv run mypy src/llm_compat`：Success: no issues found in 17 source files，退出码 **0**。
- `uv run ruff check .`：退出码 **1**，唯一报错为 `collector/tests/test_api.py:85:24` 的存量 E741（`l`），与本次 diff 无关，按任务卡记入 backlog，不作为 finding。
- `git diff --check 7f9e349..1720056`：退出码 **0**。

辅助 graphify 代码-only 扫描在 H0 快照临时目录完成（70 个代码文件，1154 nodes，2843 edges）；查询结果确认 `SyncLLMClient` 与 `LLMClient` 都经 `BaseClient` 共享编排路径。默认文档扫描因无语义抽取密钥失败，未影响代码-only 审查，也未在被审工作树产生持久文件。

## Backlog / 非目标

- `collector/tests/test_api.py:85` 的 Ruff E741 是 main 上已有的存量噪音，未开 issue；不属于本次 diff。
- issue #24（`stats` 记账时机）、#26（collector 上报路径与覆盖面）、#27（`_pending_refusal_report` 载体与并发安全）属于任务卡明确排除的增量 2，本轮不评价、不计数。
- 设计文档 `docs/sessions/triage-20260831/design.md` 在 base/H0 均缺失，已在本报告中记录；任务卡本身提供了足够的审查不变式。

## Findings 计数

**0 P1，0 P2，0 P3。**

本轮没有 finding，因此 P1 两问无待判定对象：不存在需要判断“真实使用方式下是否触发”及“触发后果是否可接受”的候选缺陷；也没有将上述存量噪音或明确非目标误计为 P1/P2/P3。

审查结论：**PASS（第 1 轮；本批 infra 例外收敛仍需后续按规则累计连续两轮无新增 P1）。**

## Git 归档

本报告写入前执行的 `git log --oneline -1` 输出为：

`7f9e349 chore: install gate-disposition caller pinned to 267eff0688c4`

本文件随后作为新增文件提交并推送到本卡分支，以上输出与固定审查基线一致。

提交后在本分支执行的 `git log --oneline -1` 输出为：

`636f6de review: verdict for increment1 passthrough`
