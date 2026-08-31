# 增量 2 第 1 轮独立审查 verdict

## 结论

- 审查对象固定为 `8c07701..73a2470`，仓库 `llm-compat`，PR #32；审查期间不纳入任何新提交。
- 风险等级为 `personal`；本增量属于失败路径/资源账本 infra 例外，收敛条件为连续 2 轮无新增 P1，本卡为第 1 轮。
- Verdict：**通过，无阻塞性 P1**。本轮计数：**0 P1、2 P2、1 P3**；P2/P3 均为非阻塞的测试锁定或私有扩展边界问题，当前代码运行结果未发现新增 P1。
- 设计契约没有发现写错或与本次改动矛盾之处。`chat_stream()` 不记账不上报、`SyncLLMClient` 不上报 collector 按卡面和既有文档处理，不计本轮 finding。

## 审查范围与新证据

只检查了固定范围 `8c07701..73a2470` 的 diff：

```text
 CHANGELOG.md
 docs/guides/integration-guide.md
 src/llm_compat/_base.py
 src/llm_compat/client.py
 src/llm_compat/sync.py
 tests/test_refusal_reporting_paths.py
 tests/test_stats_accounting.py
```

本轮相对已有主脑分诊新增并实际核验的证据：

1. 固定 SHA 的完整 diff、最终代码行号、所有新增测试和调用点的逐行走查。
2. 独立 `ocr-review`：返回 `status=partial`、`cli_status=partial`、`coverage=partial`，profile 为 `minimax/MiniMax-M3`，共 6 条工具 finding；其中 2 条被复核器 refuted，4 条未完成复核。它不是“扫过且干净”，以下逐条落地分诊。
3. 在 `73a2470` 临时工作树 `/tmp/llm-compat-review-r1-271912` 执行：完整测试 1266 passed；mypy 通过；拒绝上报路径测试连续 5 次均通过。
4. 在两个独立的 `8c07701` 临时工作树中，每个只拷入一个新增测试文件；先 grep 命中目标测试，再 `--collect-only` 收集到 1 条，最后目标测试均红。具体留痕见“红验”。
5. 对 `pre_request=False` 做了 async/sync 实测，结果均为 `0/0/0`；这是本卡未改动的既有入口边界，记 backlog，不冒充增量 finding。

## 测试与静态检查记录

运行环境：Python 3.14.3、pytest 9.0.3、mypy 1.x（由 `uv` 环境解析）。

| 命令 | 结果 |
|---|---|
| `uv run pytest` | 退出码 `0`，`1266 passed in 3.93s` |
| `uv run mypy src/llm_compat` | 退出码 `0`，`Success: no issues found in 17 source files` |
| `uv run pytest tests/test_refusal_reporting_paths.py` 第 1 次 | 16 passed，退出码 `0` |
| 第 2 次 | 16 passed，退出码 `0` |
| 第 3 次 | 16 passed，退出码 `0` |
| 第 4 次 | 16 passed，退出码 `0` |
| 第 5 次 | 16 passed，退出码 `0` |
| `uv run ruff check .` | 退出码 `1`；唯一错误是存量 `collector/tests/test_api.py:85` 的 E741，按卡面记 backlog，不计 finding |
| `git diff --check 8c07701..73a2470` | 退出码 `0` |

## Findings

### F-P2-01：整链上报顺序测试只比较第一条 occurrence

- 级别：P2，非阻塞，建议修测试断言。
- 违反：不变式 2（整链 `ContentPolicyError` 抛出前完成 collector 上报）和不变式 5（中间被判拒模型全部上报）的测试锁定要求；代码本身当前按顺序上报。
- 位置：`tests/test_refusal_reporting_paths.py:133-135`。断言是 `order.index("collector") < order.index("exception")`，只约束第一个 collector 标记；虽然紧邻的 `len(recorder.posts) == 2` 能覆盖当前同步 mock 的常见漏报，但没有把“全部 collector 标记都在 exception 前”写成断言。
- 失败场景：两个模型均返回 `finish_reason=content_filter`，若未来把第二条上报改成延迟任务，实际顺序可能是 `collector → exception → collector`；当前 `index` 断言仍只看第一条，测试可能给出错误绿，无法锁死“全部上报先于抛错”。应改成最后一个 collector 的位置小于 exception 的位置，或等价的全量位置断言。
- P1 两问：①真实使用方式下会触发吗？当前生产代码不会触发，风险发生在未来错误修改测试所保护的上报顺序时；作为测试缺口会触发。②触发后果能否接受？不会直接造成当前用户数据错误，但会让 CI 对整链上报时序给出假绿，低于本仓 P1 红线，因此定为 P2。
- 当前代码核验：`_finish_driven_chat` 在 `src/llm_compat/client.py:121-125` 先刷新拒绝上报，再调用 `on_error` 或 `on_success`；`_report_call_refusals` 在 `:156-175` 对列表逐条 `await`，当前实现满足顺序。

### F-P2-02：路径×终态矩阵仍有未锁死的网络/同步组合格

- 级别：P2，非阻塞，建议补回归测试。
- 违反：不变式 1 的“每条逻辑调用恰好一条 stats 记录”测试覆盖要求；这是测试缺口，不是已观测到的当前错误。
- 位置：生产统一终态记账位于 `src/llm_compat/_base.py:238-246`、`:691`、`:902`、`:943-948`、`:976-981`；新增测试主要位于 `tests/test_stats_accounting.py:49-406`。
- 具体缺口与失败场景：向 `chat_json()`、带 fallback 的 `chat_json()`、`chat_image()` 或 sync 对应路径注入 `httpx.ConnectError`，当前实现应输出 `total_calls=1, success_count=0, error_count=1`，但新增矩阵没有为这些组合逐格锁死；未来若某个 wrapper 绕过 `_record_terminal_error`，完整套件仍可能通过而错误输出 `0/0/0`。同样，async `chat()` 的多模型整链拒绝只由 `tests/test_stats_accounting.py:62-69` 锁定了单模型拒绝，未单独锁定带 fallback 的 chat 整链计数。
- P1 两问：①真实使用方式下会触发吗？当前实现未触发；若后续只改某个未覆盖 wrapper，真实网络错误或整链拒绝会触发。②触发后果能否接受？会让统计账本漏记，属于需要修复的契约回归，但当前只是测试覆盖不足，不能把假设中的未来回归升级为本轮 P1；按测试质量和可观测性定为 P2。
- 现状判断：共享编排器的异常分支统一进入 `_record_terminal_error`，所以当前代码行为正确；本 finding 不要求本轮改产品代码。

### F-P3-01：私有 `_call_effects` 参数允许扩展方复用陈旧容器

- 级别：P3，非阻塞，接受不修；标准公开 API 路径不会触发。
- 违反：不变式 3 的“拒绝状态按调用唯一”要求，适用范围仅是主动调用私有 orchestrator 的扩展方或测试。
- 位置：`src/llm_compat/_base.py:575-578` 接受外部传入的 `_call_effects`，并在 None 时创建新容器；公开入口分别在 `src/llm_compat/client.py:137-144`、`:190-199` 和 `src/llm_compat/sync.py:106-111`、`:134-141` 创建新实例。
- 失败场景：自定义内部扩展先创建 `shared = _CallSideEffects()`，再把同一个 `shared` 传给两次 `_content_fallback_orchestrator`；两次调用的拒绝记录会进入同一个 `refusal_reports` 列表，随后一次刷新可能把另一调用的 model/layer 一并上报，造成证据错配。
- P1 两问：①真实使用方式下会触发吗？普通调用方只能使用公开 `chat`/`chat_json`/`chat_image`，不会进入该私有参数；只有主动耦合下划线私有方法的扩展会触发。②触发后果能否接受？若私有扩展确实复用容器，证据错配不可接受，但触发前提不属于本仓真实标准用法，且错误是可见的 collector 错配而非静默业务结果错误，因此为 P3。
- `None` 兜底结论：`_content_fallback_orchestrator` 每次生成器实例首次运行时在 `:578` 创建新的 `_CallSideEffects`；无默认可变参数、无类属性、无闭包共享，公开入口也不走此分支。若私有调用者直接使用 None，拒绝证据只留在该次编排器局部列表，不会被任何 public driver 刷新到 collector，但 `ContentPolicyError.evidence` / `attempt_layers` 仍由终态异常保留；这解释了该分支的证据去向。

## 三个降层审查问题

### 1. 终态写入成功之前发生了什么不可逆动作？

按实际代码顺序核验如下：

1. 入口先执行 `pre_request`：async 在 `client.py:136`、sync 在 `sync.py:105`/`:133`。它早于 effects 和编排器；若 hook 返回 False，直接抛 `SkipRequestError`，既不发请求也不记账，这是存量行为，见 backlog。
2. 每次尝试在 `_simple_attempt` 的 `:459-460` 或 `_json_attempt` 的 `:505-506` 输出 request INFO；`_extract_result` 在 `:392-396` 输出 response INFO。它们早于终态判断，但只输出 request id、模型、provider、thinking、数量、延迟和 token 数，不输出 prompt/response 正文。
3. JSON 路径在 `:545` 先记过程计数 `json_parse_failure`，拒绝路径在 `:782`/`:822` 写 WARNING；每次拒绝在 `:785-796` 或 `:864-875` 只把证据追加到调用级内存容器，不是 collector 外部上报。
4. 拒绝 fallback 的过程计数在 `:909` 发生；它不是一次调用的 success/error 终态账本。救援 parse/schema 成功后，先在 `:683-691` 完整设置 rescue 结果、trace 和 refusal 字段，再执行 terminal success 记账。
5. 正常成功先在 `:895-902` 设置 trace/fallback 字段，再调用 `_record_terminal_success`；解析失败、HTTP/网络错误或其他异常进入 `:947-957`，拒绝终态进入 `:929-944`，超时耗尽后进入 `:962-981`。这些终态记录点均在 generator 向 driver 抛出/返回之前。
6. terminal stats 写入后，async driver 在 `client.py:121-125`（失败）或 `:124-126`（成功）刷新 collector；具体 POST 在 `:156-173`。失败路径 collector 之后才调用 `on_error` 并重新抛错，成功路径 collector 之后才调用 `on_success`。sync 版不刷新 collector，按既有边界处理，但 stats 仍已由共享编排器完成。

结论：当前没有“先 collector 上报、再判定最终失败”的路径。被判拒时的 `_note_refusal_report` 是内存记账，不是外部上报；中间拒绝是否最终成功由 fallback/rescue 决定，只有终态确定且 terminal stats 已写后才 POST。日志和过程计数确实早于终态，但日志不含正文，且它们没有被错误地当作 success/error 终态记录。

### 2. 守卫值在实际部署形态下是否唯一？

- `_CallSideEffects.refusal_reports` 使用 `field(default_factory=list)`（`_base.py:155`），没有默认参数共享；类没有该实例字段的类属性，闭包只捕获当前 generator 的局部 `effects`。
- async `chat` 与 `chat_json` 各自在线程/任务调用内创建实例（`client.py:137`、`:190`）；`chat_image`/`chat_images` 委托到一次新的 `chat`（`:234-257`）。sync chat/chat_json 同理（`sync.py:106`、`:134`），图片入口委托到 sync chat（`:151-162`）。
- `_chat_orchestrator` 和 `_json_chat_orchestrator` 仅把同一调用的 effects 向下传递（`_base.py:989-998`、`:1111-1153`），不存在生成器复用或跨调用缓存。并发测试连续 5 次通过，且 payload 按 model/layer 对回自己的调用。
- `_call_effects=None` 兜底（`:578`）在当前公开路径不会触发；私有直接调用时每个编排器生成器都新建容器，但没有 public driver 会替它上报，因此证据只进入该调用最终异常的公开 evidence 字段，拒绝报告列表本身在调用结束后丢弃。
- 唯一例外是 F-P3-01：主动传入同一个预建 effects 可以制造共享。这不是默认参数、类属性、闭包或生成器复用导致的标准路径，而是新加的私有扩展面。

### 3. 保护的是“写入”还是“行为”？

当前保护不只靠调用点记得调：`_extract_result` 不再记 success（`_base.py:364-408`），success 统一由编排器在正常返回 `:902` 或 rescue 返回 `:691` 调用 `_record_terminal_success`；error 统一在 CP terminal `:943`、generic exception `:948` 或 loop exhausted `:976` 调用 `_record_terminal_error`。新增 helper 的调用点覆盖了 async 与 sync 的共享编排器，且新增统计测试把失败结果锁定为一条。

仍然存在两个明确绕过点，但都不是本卡引入：

- `chat_stream()` 在 `client.py:201-232` 直走 SSE，不进 `_content_fallback_orchestrator`，保持 0 条 stats；卡面明确把它列为既有行为。
- `pre_request` 在公开入口 `client.py:136`、`sync.py:105`/`:133` 先于编排器；返回 False 时逻辑调用没有 stats 记录。卡面没有授权本轮扩展该行为，故列 backlog。

除这两个显式边界外，`chat`、`chat_json`、`chat_image`、`chat_images`、sync chat/chat_json/chat_image 以及 rescue 都进入上述终态 helper；没有发现新增路径绕过 helper 却仍被算作一次逻辑调用。

## stats 路径 × 终态矩阵

“不适用”均经过代码核实，不是用来掩盖空测试格；“实现正确但未锁死”明确列出缺口。

| 路径 | 成功 | JSON 解析失败 | 整链拒绝 | HTTP 或网络错误 |
|---|---|---|---|---|
| async `chat` | `:902` → 1 success；`tests/test_stats_accounting.py:53-60` 锁死 | 不适用：走 `_simple_attempt`（`:447-475`），不调用 `parse_json`；上游 response JSON 解码失败属于 transport/general error，不是本地 JSONParseError 格子 | 单模型整链在 `:62-69` 锁死为 1 error；带 fallback 的多模型 chat 计数未单独锁死（F-P2-02） | HTTP fatal `:71-80`，network `:96-105`，均锁死为 1 error |
| async `chat_json` 无 fallback | `:902`；`:112-119` | `:948`；`:121-132` | `:943`；`:150-157` | HTTP fatal `:159-168`；network 组合未单独测试（F-P2-02），实现由 `:948` 覆盖 |
| async `chat_json` 有 fallback/救援 | rescue success `:691`，普通 fallback success `:902`；`:175-203` | 最后模型 parse failure `:948`；`:235-249` | `:943`/`:976`；`:221-233` | HTTP fatal `:251-264`；network 组合未单独测试（F-P2-02） |
| async `chat_image` / `chat_images` | 委托 `client.py:234-257` → `:902`；`:270-290` | 不适用：委托 `chat`，不做本地 JSON 解析 | 委托 `chat` 的拒绝终态 `:943`/`:976`；`:292-301` 是单模型 | HTTP fatal `:303-313`；network 组合未单独测试（F-P2-02） |
| sync `chat` / `chat_json` / `chat_image` | sync wrapper `sync.py:97-162` 进入共享 `:902`；成功由 `:359-406` 锁死 | sync `chat_json` 进入 `:948`；`:367-374` 锁死 | sync `chat` 单模型拒绝进入 `:943`；`:376-382` 锁死；sync chat_json/fallback multi-model 未单独锁死（F-P2-02） | sync chat HTTP fatal 进入 `:948`；`:384-392` 锁死；sync network、chat_json/image error 未单独锁死（F-P2-02） |

过程计数 `fallback_count`、`json_parse_failures` 不计入一次调用终态；`chat_stream()` 明确不在本矩阵内，保持既有 0/0/0 行为。

## 嵌套 try/except 记账逐条走查

1. 结构化拒绝或 HTTP 内容策略错误：`inner.send(response)` 在 `_base.py:876` 或 HTTP 分支 `:797-802` 抛 `ContentPolicyError`，外层 `except ContentPolicyError` 在 `:906` 捕获。它先把当前模型加入 `attempted`（`:908`）并记 fallback 过程计数（`:909`）。如果还有模型，`:945` `continue`，当前拒绝不记终态 error；最后一个模型在 `:929-944` 构造包含 evidence/attempt_layers/trace 的终态异常，`:943` 只记一次 error 后 `raise`。`except ContentPolicyError` 在 `except Exception` 前，因此不会被 generic 分支再记一次。
2. fallback 链耗尽但救援成功：CP except 在 `:926-928` 调 `rescue_best_candidate()`；解析和 schema 成功时 `:683-691` 返回并只记一次 success，CP except 不会继续构造 error。
3. fallback 链耗尽且救援 parse/schema 失败：救援内部 `:667-681` 捕获异常，只写 `rescue_failure`，不记账；控制返回 CP except 后在 `:929-944` 统一构造/记一次 terminal error。没有“救援失败一次 + 链耗尽一次”的双记账。
4. JSON 解析失败：`_json_attempt` 的 `:535-554` 将底层异常转换为 `JSONParseError`；若 self-correction 仍有次数则 `:556-565` 继续请求，最终异常由外层 `except Exception` 的 `:947-957` 记一次 error。解析过程计数 `:545` 可能多次增加，但不增加 `total_calls`。
5. HTTP/网络/其他异常：`response.error` 经 `inner.send` 传播到 `:947-957`，先由 `:948` 记一次 error，再补齐 `LLMCallError` 的 error_kind/http_status/trace（`:949-956`），最后向 driver 抛出；不会落到底部 exhausted 分支。
6. 已有 attempt 后因 deadline break：循环在 `:699-703` 退出，底部先尝试 rescue（`:959-961`），失败后在 `:962-981` 构造终态 CP error，`:976` 记一次后抛出。若 `last_error` 存在，`:979-980` 从它 raise；仍不重复记账。

结论是：CP 分支和 generic 分支互斥，rescue success/failed 两条也互斥，正常成功与异常终态各只有一个 terminal helper。没有发现“一次都不记”或“记两次”的新增路径；已有 `pre_request`/stream 边界已在前文和 backlog 单列。

## collector 上报与证据完整性

- 结构化拒绝每个模型在 `_base.py:844-880` 生成自己的 evidence、finish_reason、layer 和 report；HTTP 内容策略拒绝在 `:776-802` 生成 `http_error` report。
- 每条 report 都 append 到调用级列表（`:248-275`）；fallback 链不会覆盖前一条，因此整链全部 refusal 都在 `effects.refusal_reports` 中。
- 失败路径 `client.py:120-123` 先刷新 reports，再调用 `on_error` 并 re-raise；成功/救援路径 `:124-126` 先刷新 reports，再调用 `on_success`。`CollectorClient.report_refusal` 自己按既有 fail-open 契约处理 collector 故障，`_report_call_refusals` 的 `:157-175` 也不会让 collector 故障替换原始 LLM 异常。
- 未配置 collector 时 `:152-153` 直接返回，不产生额外异常；原始 `ContentPolicyError.evidence` 和聚合的 `attempt_layers` 在 `_base.py:929-944`/`:962-981` 保留。新增无 collector 测试在 `tests/test_refusal_reporting_paths.py:217-236` 验证。
- INFO 日志只含 metadata；拒绝 WARNING 允许既有 evidence matched_text，卡面约束仅是默认 INFO 不出现正文。新增 caplog 测试 `:364-385` 验证 prompt/response 不进 INFO。

## 熵增审查

| 新增项 | 第二消费者/真实必要性 | 判断 |
|---|---|---|
| `_CallSideEffects` | async `chat`、async `chat_json`、sync 两个入口各创建并向共享编排器传递；承载同一调用的多个 refusal reports | 不是单实现接口；消除了 client 实例级 last-write-wins 状态 |
| `_finish_driven_chat` | async `chat` 和 async `chat_json` 共用；统一 success/error 两个终态的上报与 hook 顺序 | 有真实第二消费者，避免两入口重新分叉 |
| `_note_refusal_report` | HTTP refusal 与 response refusal 两个写入点共用；统一 payload shape | 有第二消费者，删除重复 dict 构造 |
| `_record_terminal_success` | 正常成功 `:902` 与 rescue 成功 `:691` | 两条真实终态消费者，解决 `_extract_result` 提前记 success |
| `_record_terminal_error` | CP terminal `:943`、generic exception `:948`、loop exhausted `:976` | 多个真实终态消费者，集中保持一条 error 口径 |

没有新增配置项、依赖或转发-only 公共层。`_call_effects` 是私有内部参数；它带来的扩展注入风险已单列 F-P3-01，但不构成熵增 P1。

## OCR 与主脑输入 finding 的独立分诊

### 本轮独立 `ocr-review` 返回的 6 条

| 工具意见 | 独立结论 |
|---|---|
| `_report_call_refusals` 重复提取 `model`/`fallback_chain` | Refuted。`client.py:154-155` 各读取一次不同字段，没有两个调用点或重复提取。 |
| 多模型 refusal report 串行 POST，建议 gather | 非 finding。当前 `await` 顺序是为了满足全部上报先于 raise；没有延迟上限契约，且并发 collector 会改变顺序/失败语义。 |
| success path 中 collector report 在 `on_success` 前，较旧顺序相反 | 非 P1/P2 finding。当前顺序是“终态 stats → collector → hook”，两者都在终态后；文档没有承诺 hook 与 collector 的相对顺序，不能仅凭存量顺序反着 spec。 |
| `test_all_refused_reports_before_raise` 只比较第一个 collector | 确认，落为 F-P2-01。 |
| 并发测试 monkeypatch 没有调用计数 | 非 finding。wrapper 直接包住当前真实 `client._request`，5 次连续运行通过；未来重构测试注入点时可作为测试维护建议。 |
| INFO caplog 不检查 WARNING/ERROR/DEBUG/structured extra | 非 finding。卡面契约明确是默认 INFO；`refusal.py` 的拒绝 WARNING 是存量且不在本次允许审查逻辑内。 |

### 卡面附带的 8 条 OCR 输入逐条复核

1. `client.py:118` 异常分支 report 可能替换原异常：实际最终代码为 `:120-123`，report loop 内的 extract/POST 在 `:157-175` 全部捕获；正常 `effects`、collector 和 getattr 均不抛出可替换原异常的路径，refuted。
2. `client.py:137` 显式 `_call_effects` 与 `**extra` 同名：私有参数被主动重复传入会产生 fail-loud `TypeError`，不静默且不属于正常 API；低风险 P3 边界，与 F-P3-01 同属私有注入面。
3. `client.py:156` `report["model"]` 下标：report 由 `_note_refusal_report` `:262-275` 内部构造且必含 key；不成立。
4. `tests/test_refusal_reporting_paths.py:94-104` grep 私有字段：这是卡面明确要求的源级加固，且并发行为由 `:260-315` 的 model/layer payload 对照测试独立锁死；不是 finding。
5. `tests/test_refusal_reporting_paths.py:28` 假设 src-layout：本仓 `src/llm_compat` 布局与 pyproject 一致，测试在仓库内运行；不成立。
6. `tests/test_refusal_reporting_paths.py:379-383` caplog 只过滤 INFO：与契约“默认 INFO 不出现正文”一致；不成立。
7. `_base.py:927` rescue parse failure 的 model 归属改变：`LLMStats` 没有 per-model error 统计，只有 error type/refusal count；没有可观测差异，不成立。
8. `_base.py:578` 外部传入陈旧 `_CallSideEffects`：私有扩展确可复用同一容器，确认并落为 F-P3-01；公开入口每次新建，故不升级。

## Backlog（不计本轮 finding）

- `chat_stream()` 当前不记账、不上报 collector；卡面明确这是本卡不改的既有行为。
- `SyncLLMClient` 当前不向异步 `CollectorClient` 上报；卡面明确不计本轮。
- `pre_request` 在入口早于 effects/编排器，失败时 stats 为 `0/0/0`；这是本次 diff 前已有行为。实测 async/sync 均为 `0/0/0`。
- `uv run ruff check .` 的 `collector/tests/test_api.py:85` E741 是 main 上的存量噪音。
- collector 多条 report 串行等待可能增加拒绝返回延迟，但卡面只要求上报先于 raise，没有时延契约；不作为本轮 finding。

## 红验留痕

红验严格在 base `8c07701` 临时工作树执行，每个工作树只拷入对应的一个新增测试文件：

1. `/tmp/llm-compat-red-report-336539`：只拷入 `tests/test_refusal_reporting_paths.py`。
   - grep 先命中：`112: async def test_all_refused_reports_before_raise`。
   - `uv run pytest --collect-only -q 'tests/test_refusal_reporting_paths.py::TestRefusalReportingPaths::test_all_refused_reports_before_raise[chat_json]'` 退出码 `0`，输出 `1 test collected`。
   - 运行目标测试退出码 `1`，失败于 `tests/test_refusal_reporting_paths.py:133`：base 的 `order` 为 `['exception']`，没有 collector 上报。
2. `/tmp/llm-compat-red-stats-336539`：只拷入 `tests/test_stats_accounting.py`。
   - grep 先命中：`121: async def test_parse_failure_records_error_not_success`。
   - `uv run pytest --collect-only -q 'tests/test_stats_accounting.py::TestChatJsonNoFallbackAccounting::test_parse_failure_records_error_not_success'` 退出码 `0`，输出 `1 test collected`。
   - 运行目标测试退出码 `1`，失败于 `tests/test_stats_accounting.py:129`：base 观察到 `success_count=1`、`error_count=1`、`total_calls=2`，证明测试实际执行且锁定本次改动。

## 交付留痕

报告写入前的现场命令输出：

```text
$ git log --oneline -1
8c07701 Merge pull request #29 from zlxlabs/card/llm-compat-20260831-01
```

本文件是本轮唯一新增的仓内文件；提交后再用 `git log --oneline -1` 校验本报告已落入 `card/llm-compat-20260831-04` 分支并推送远程。

