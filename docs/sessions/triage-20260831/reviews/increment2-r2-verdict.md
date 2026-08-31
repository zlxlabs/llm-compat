# 增量 2 第 2 轮独立审查 Verdict

审查对象（H0 冻结）：`8c07701..b1d86ef`（PR #32）。
- `8c07701..73a2470` = 原始实现（H0）
- `73a2470..b1d86ef` = 第 1 轮 finding 的修复增量（H1）

风险等级：`personal` + infra 例外（连续 2 轮无新增 P1）。本卡是第 2 轮。只审、不改产品代码。

## 本轮新证据（换家 + 换证据源 + 换视角）

第 1 轮是正向核验「不变式成立吗」。本轮不采信第 1 轮推理链，也不把同一份 diff 再读一遍当新证据。本轮实际新增的证据源：

1. **H1 修复提交** `4b4e46e`、`b1d86ef`（第 1 轮之后才存在的 diff）。
2. **反向攻击脚本** `/tmp/llm-compat-r2-attacks/attack_reverse.py`：在 H1 快照 `/tmp/llm-compat-r2-review`（detached `b1d86ef`）上主动构造能打破不变式的输入、时序和调用方式；脚本不是复核已有测试。
3. **断言约束力改坏实验**：临时改 `_report_call_refusals` 把第二条上报延后，独立跑出 `order=['collector', 'exception', 'collector']`，再还原；`git status` 干净。
4. **H1 全量测试 / mypy / 上报测试连续 5 次 / OCR envelope**（`status=reviewed`，3 条工具意见全部用本仓 P1 两问重判）。

## 结论

**本轮 0 P1 / 1 P2 / 0 P3。** 反向攻击没有打出「完成的逻辑调用记 0 条或 2 条」「跨调用串证」「没配 collector 时 evidence 丢失」。打得出来的是：上报 `await` 插在 `ContentPolicyError` 再抛之前，任务取消会把 CPE 换成 `CancelledError`。这不是静默成功，两问后定为 P2，不阻断 infra 例外的「无新增 P1」收敛计数。

H0..H1 专项审通过（只修登记在案的 F-P2-01 / F-P2-02，无夹带、无新抽象、无双路径生产逻辑）。F-P2-01 新断言有约束力，已独立复现，不采信执行器自述。

## 反向视角任务（逐条作答）

攻击入口一律走公开 API（`chat` / `chat_json` / `chat_image` / `chat_images` / `SyncLLMClient`），不传 `_call_effects`。httpx 用 `MockTransport` 注入。完整输出见 `/tmp/llm-compat-r2-attacks/attack.out`。

### 1. 让 `total_calls != success_count + error_count`

**没构造出来。** 所有完成的逻辑调用都保持 `total == success + error`，且恰好 1 条终态记录。

| 构造 | 实际输出 |
|---|---|
| `chat_images` 3 张图 | `http_calls=1 content='ok'`，`stats={total:1, success:1, error:0, balanced:True}` |
| `self_correction` 先 2 次坏 JSON 再成功 | `http_calls=3`，`json_parse_failures=2`，`stats={total:1, success:1, error:0}` |
| `self_correction` 耗尽 | `JSONParseError`，`stats={total:1, success:0, error:1}` |
| `max_retries=2` 网络错误耗尽 | `RetryableError`，`stats={total:1, success:0, error:1}` |
| HTTP 中途 `task.cancel()` | `CancelledError`，`stats={total:0, success:0, error:0, balanced:True}` |
| `chat_image` 再 `chat` | 先 1 后 2，各成功，平衡 |

**`chat_images` 算几次逻辑调用：** 实现是组一条多图 message 再委托一次 `chat()`（`client.py:247-257`），一次 HTTP、一条 stats。文档 `integration-guide.md` 把 `chat_images` 与 `chat_image` 并列写成「一次逻辑调用一条记录」。卡面不变式 1 点名的是 `chat_image`，文档补了 `chat_images`；实现和文档一致，不是「按图 N 次」。不把卡面省略写成契约错误。

HTTP 中途取消得到 0/0/0：调用没到达编排器终态，不是「完成的逻辑调用记 0 条」。调用方看到的是 `CancelledError`，不是静默成功。不记 finding。

救援路径：构造「推断拒绝 + `on_all_refused=return_best`」时，短中文拒绝文本未被判 `is_inferred`（判定逻辑属非目标 `refusal.py`），走了普通成功 1 条。`on_success` 抛错被既有 `_invoke_on_success` 吞掉并打 WARNING，调用仍返回结果；这是存量 hook 边界，不是本增量引入的双记。

`inner.close()` 出现在 `_note_refusal_report` 之前（`_base.py:784-785`、`:845-864`）。`_simple_attempt` / `_json_attempt` 无 `finally` 副作用，`GeneratorExit` 干净退出；未能构造 close 抛错导致 0 记或 2 记。

### 2. 让一次调用的证据出现在另一次调用的上报里

**没构造出来。** 公开 API 下并发调用各自上报自己的 model / preview。

| 构造 | 实际输出 |
|---|---|
| 同一 client 上 `asyncio.gather` 混合 `chat`×2 + `chat_json`×1，三条都拒 | `reported models=['gpt-4o-aaa','gpt-4o-bbb','gpt-4o-ccc']`，preview 与 model 一一对应，`mismatched=[]`，`stats={total:3, success:0, error:3}` |
| 一次抛 CPE、另一次仍在 await HTTP | 快路径 `attempt_layers={'fast-model': ...}`，慢路径返回 `SLOW-OK`；collector 只有 `fast-model` |
| `chat_images` 与 `chat_json` 并发 | `models=['img-model','json-model']`，`previews=['REFUSED-IMG','REFUSED-JSON']` |
| 两个 event loop / 两个 client 同 `collector_url` | 各报自己的 model。公开 API 无法把同一活着的 `LLMClient` 塞进第二个 loop（httpx 绑定 loop），攻击停在传输层，到不了 `_CallSideEffects` |
| `SyncLLMClient` 与 async 客户端同 `collector_url` | sync 有 collector 实例但不上报（既有边界）；async 只报 `async-model`；两边 CPE 的 `evidence` / `attempt_layers` 都在 |

`_pending_refusal_report` 已从实例字段删除（H0 源码与测试锁死）。公开 `chat(..., _call_effects=...)` 会进 `**extra` 再与显式 `_call_effects=` 撞车成 TypeError，注入不了共享容器。

### 3. 让上报发生在异常抛出之后，或让应上报的拒绝完全不上报

构造了三种，完成的 CPE 路径没有「先 exception 后 collector」。

**3a. 第一条 `report_refusal` 抛 `Exception`：**  
`order=['collector-fail-0', 'collector', 'exception']`，posts 只剩第二条模型 `gpt-4o`。`except Exception: pass` 是按条吞错（H0 单条上报就有这层，H0 改成循环后后续条目仍发）。CPE 照抛，`attempt_layers` 两模型都在。第一条 sidecar 丢了，不是 last-write-wins，也不是静默成功。不升 P1。

**3b. 第二条上报中 `CancelledError`（`BaseException`，穿过 `except Exception`）：**  
`got CancelledError`，`order=['collector'] posts=1 cancelled_during=[1]`，posted 只有 `deepseek-v4`，`stats={total:1, success:0, error:1}`。账本已在编排器终态记过 error；`_invoke_on_error` 被跳过；调用方拿到 `CancelledError` 而不是 `ContentPolicyError`。见 **F-P2-R2-01**。

**3c. 第一条上报中取消：**  
`posts=0`，`CancelledError`，stats 仍 1 error。同 F-P2-R2-01。

正常 CPE 路径（3a 未取消）上报全部发生在 `order` 里的 `exception` 之前。

### 4. 让 `evidence` / `attempt_layers` 在没配 collector 时丢失

**没构造出来。** 没配 collector 时失败路径只抛 `ContentPolicyError`，不额外报错。

- `chat()` 整链：`evidence.layer='structured_signal'`，`attempt_layers={'deepseek-v4':..., 'gpt-4.1-mini':...}`，`raw_content` 仍在。
- `chat_json()` HTTP `content_policy_violation`：`evidence.layer='http_error'`，`attempt_layers={'gpt-4o':'http_error'}`，`http_status=400`。
- `chat_image()`：`evidence` 与 `attempt_layers={'gpt-4o':...}` 都在。
- INFO 日志：`leaked=[]`。样本只有 `LLM request | model=... | messages=1` 和 token 计数，无 prompt/response 正文。

## H0..H1 修复增量专项审（`73a2470..b1d86ef`）

1. **是否只修 F-P2-01 与 F-P2-02？** 是。`git diff --stat 73a2470..b1d86ef` 只有两个测试文件：`tests/test_refusal_reporting_paths.py`（+6/-2）和 `tests/test_stats_accounting.py`（+87）。`4b4e46e` 把整链拒绝顺序断言从「第一个 collector 在 exception 前」改成「最后一个 collector 在 exception 前」；`b1d86ef` 补网络错误与多模型整链的记账格。无 src / 文档 / 配置夹带。
2. **是否新增未经批准的抽象？** 否。无新 helper、无新 fixture 层、无新配置项。
3. **状态 / 事实源 / fallback 是否无依据增加？** 否。生产代码零 diff。
4. **是否留下双路径？** 生产无新旧并存。测试上 `test_chat_image_refusal_reports_before_raise` 仍用 `order.index("collector") < order.index("exception")`，但该用例只有 1 条上报，与「最后一个 collector」语义相同，不是同一格两种期望。

H1 增量审通过，不按新增 P1 计入收敛。

## 改坏实验（独立复现，不采信报告自述）

对象：`tests/test_refusal_reporting_paths.py` 的 `test_all_refused_reports_before_raise`（H1 `4b4e46e` 引入 `collector_at[-1] < exception_at`）。

做法（临时 worktree `/tmp/llm-compat-r2-review`，**不是**本卡工作树）：

1. `sed -n '183p'` 确认注入点含 `# RED-VERIFY`。
2. 把 `_report_call_refusals` 改成：前面的条目 `await`，最后一条 `asyncio.create_task`（挂在实例上防 3.14 回收）。
3. 独立脚本 `/tmp/llm-compat-r2-attacks/red_verify_order.py` 在调用方看到异常之后再 `await` 那条延迟任务，打印 `order` 并分别评估新旧断言。

实际输出：

```
method=chat order=['collector', 'exception', 'collector'] posts=2
  collector_at=[0, 2] exception_at=1
  OLD assert index(collector) < index(exception): 0 < 1 -> True
  NEW assert collector_at[-1] < exception_at: 2 < 1 -> False
  (equivalent to assert 2 < 1)
method=chat_json ... 同样 2 < 1 -> False，旧断言仍 True
```

新断言在「第二条上报被延到 exception 之后」时变红；旧断言 `0 < 1` 仍绿。约束力成立。

还原：`git checkout -- src/llm_compat/client.py`；`git status` = `nothing to commit, working tree clean`；`grep RED-VERIFY` 无命中。本卡工作树全程未改产品代码。

原测试文件里 `assert len(recorder.posts) == 2` 写在顺序断言之前，且测试自己不等待未 await 的 task，所以直接 `pytest` 该用例会先死在 `1 == 2`，到不了顺序断言。这不否定上面脚本对「order 交错」的直接求值。两条断言的约束力差就是 `2 < 1` vs `0 < 1`。

## 熵增审查

对照坏味道词表，问 diff 里每个新增物有没有第二消费者。

| 新增物 | 消费者 | 判定 |
|---|---|---|
| `_CallSideEffects` | async `chat` / `chat_json`（及委托它们的 `chat_image`/`chat_images`）、sync `chat` / `chat_json`、编排器 `_content_fallback_orchestrator` | 有真实第二消费者。它就是 #27 要的调用级容器，替代实例字段 `_pending_refusal_report`。不是镜像状态。 |
| `_finish_driven_chat` | `chat` 与 `chat_json` 两处 | 装的是「上报再抛 / 上报再 on_success」这条不变式 2 序列，不是转发-only。 |
| `_note_refusal_report` | HTTP 拒绝与响应拒绝两处（`_base.py:785`、`:864`） | 把同一份上报 dict 收口，避免两处手写分叉。 |
| `_record_terminal_success` | 救援成功 `:691`、正常成功 `:902` | 两处终态，一行 `record_success` 的命名入口；有第二调用点。 |
| `_record_terminal_error` | 末模型 CPE `:943`、非策略异常 `:948`、循环后超时/耗尽 `:976` | 三处终态，不是单调用者包装。 |
| `_report_call_refusals` | `_finish_driven_chat` 的成功路径与失败路径 | 失败路径是本增量相对旧 `_maybe_report_refusal`（只挂成功路径）的第二消费者。 |
| `_call_effects` 可选参数 | 公开入口创建容器后下传；编排器 `None` 时自建（F-P3-01 已 backlog） | 自建是防御扩展方直调编排器，不是新配置项。 |

Sync 路径创建 `_CallSideEffects` 却从不 drain（`report_refusal` 是 async，既有边界）。容器被记满再丢弃，不是跨调用共享，也不丢异常字段。不单开 finding。

没有新的公开接口、配置键、或与 `stats` 并行的第二本账。

## OCR 前置扫描

`ocr-review --repo /tmp/llm-compat-r2-review --from 8c07701 --to b1d86ef --audience agent --concurrency 4 --background-file /tmp/ocr-bg-inc2.md`

Envelope：`status=reviewed`（不是 skipped），`findings: 3`，`verify_status=partial`（2 条复核超时）。工具 severity 只当输入。

| 工具标注 | 本仓判定 | 两问 |
|---|---|---|
| tests 两条「rescue parse failure / all refused」字节级重复，severity=medium | **不成立**。一个用 `_refusal_text_response()`（推断层，rescue 会进 `parse_candidate` 再失败），一个用 `_content_filter_response()`（结构化 `content_filter`）。不是同一格。 | — |
| `CancelledError` 在上报 await 中替换原异常并跳过 `on_error`，severity=high | **P2**（F-P2-R2-01）。不是 P1：不是静默成功，账本已记 error。 | ① `asyncio.wait_for(chat(), timeout=T)` 在整链拒绝后 collector 仍挂起时会触发。② 后果是超时/取消而非 CPE，可能按超时重试；可接受为 sidecar 路径的误分类，不可接受为 P1 静默错。 |
| 所有上报共用终态 `fallback_model`/`fallback_chain`，severity=medium | **不成立**。不变式 5 要的是「中间模型全部上报、不再 last-write-wins」，不是「每条上报自带当时剩余链」。各条仍有自己的 `model`。把终态 fallback 填进每条是上下文，不是串证。 | — |

## Findings

### F-P2-R2-01：上报 await 期间的 `CancelledError` 替换 `ContentPolicyError`

- **违反：** 不变式 2（整链拒绝的全部上报发生在 CPE 抛出之前——取消时 CPE 根本抛不出来，后续条目也不报）、不变式 5（未报完）。不是 P1 红线「静默出错」：调用方仍收到异常，stats 已是 `error=1`。
- **P1 两问：** ① 真实使用下会触发吗？会。`CollectorClient` 默认 httpx timeout 5s；`asyncio.wait_for(client.chat(...), timeout=T)` 在模型已全部判拒、正在 await collector 时到期，就会走进这条。② 触发后果能否接受？对 personal 档：用户看到的是超时/取消而不是策略拒绝，可能按超时重试；collector 少几条 sidecar。**不接受当成 P1**（没有静默成功、没有丢 evidence 字段——CPE 对象建过，只是没交到调用方）、**接受记 P2 backlog**。
- **位置：** `src/llm_compat/client.py:118-126`（`_finish_driven_chat` 先 `await _report_call_refusals` 再 `_invoke_on_error` / `raise`）；`:156-175`（循环内只 `except Exception`，`CancelledError` 是 `BaseException`）。
- **失败场景：** 两条模型均 `finish_reason=content_filter`；collector 第二条 `post` hang；对该 `chat()` task 做 `cancel()`。实测：`got CancelledError`，`posted models=['deepseek-v4']`，第二条不上报，`stats={total:1,success:0,error:1}`。这是本增量把上报从「成功路径事后」挪到「raise 之前的 await」之后**新出现的窗口**；旧错误路径根本不上报，也就没有这个 await。

不修建议（供主脑，本卡不改代码）：对 `_report_call_refusals` 用 `asyncio.shield`，或在 `except BaseException` 里先 `on_error` 再把原 CPE 与取消串起来。那是修复卡的事。

### 不重复提（第 1 轮 backlog / 非目标）

- F-P3-01：私有 `_call_effects` 可被扩展方传入预建容器
- `chat_stream()` 不记账不上报
- `SyncLLMClient` 不上报 collector
- `pre_request` 返回 False 无 stats
- `collector/tests/test_api.py:85` Ruff E741

## 命令验证

均在 H1 快照 `/tmp/llm-compat-r2-review` @ `b1d86ef4f2bf67ec01d29eef6cbe686c16aee341`：

- `uv run pytest`：1272 passed，退出码 **0**
- `uv run mypy src/llm_compat`：Success: no issues found in 17 source files，退出码 **0**
- `uv run pytest tests/test_refusal_reporting_paths.py` 连续 5 次：16 passed × 5，退出码 **0 0 0 0 0**

## Findings 计数

**0 P1，1 P2，0 P3。**

第 1 轮 0 P1；本轮无新增 P1。按 infra 例外「连续 2 轮无新增 P1」，收敛条件在本轮满足（上限 3，不必再开第 3 轮——除非主脑要把 F-P2-R2-01 升格或派修）。P2 记 backlog，不阻塞合并。

审查结论：**PASS with backlog P2**（第 2 轮，换家 Cursor/Grok，反向视角）。
