---
status: consumer-reviewed-not-ready
created_at: 2026-07-12
baseline: llm-compat v0.6.0
target: observability phase 2
owners:
  producer: llm-compat
  consumer: AI Information Processor
review_gate: consumer review + TDD + two consecutive clean Codex reviews
consumer_reviewed_at: 2026-07-12
consumer_verdict: NOT READY
---

# LLM Compat 可观测性阶段二实施计划

## 1. 目的与使用方式

本文是阶段二的独立评审入口。评审者不需要先阅读阶段一的实施过程，即可判断：

- `llm-compat` 拟新增的公共契约是否足够、稳定且不泄露敏感数据；
- AI Information Processor 能否无歧义地消费、存储和展示这些事实；
- 实施顺序、兼容策略和测试门禁是否足以避免改变现有 retry/fallback 行为。

本文先供 AI Information Processor 仓库做只读审查。消费端意见合并并拍板后，才开始阶段二代码实施。

## 2. 背景与当前基线

v0.6.0 已提供模型级可观测性：

- 成功结果通过 `ChatResult.trace` 暴露不可变 `CallTrace`；
- 稳定的运行错误通过 `LLMCallError.trace` 暴露同一结构；
- `RouteDecision` 记录未必产生上游请求的路由事实；
- `ModelAttempt` 记录一次模型/格式尝试，明确**不包含**该尝试内部的 HTTP retry；
- trace 不包含 prompt、messages、payload、响应正文、headers、API key 或原始异常；
- async/sync 共用模型级编排语义；
- timeout、retry、fallback 和旧 `LLMStats` 的 response/error 记账行为未改变；旧
  `total_calls` 并不保证等于逻辑调用数。

当前缺口是：一个 `ModelAttempt` 发生多次 transport 调用时，消费端只能看见最终模型级结果，无法回答
“调用了几次上游、每次为何失败、是否发生退避、最终在哪一层结束”。此外，终态错误尚无可安全持久化的
摘要，调用方也没有统一的终态 trace hook。

### 2.1 v0.6.0 不变契约

阶段二必须保持以下语义：

1. 一个进入 shared orchestrator 且产生稳定终态的逻辑 `chat()` 调用对应一个 `CallTrace`；
   pre-request skip 和未包装未知异常可能没有 trace。
2. 一次 shared orchestrator yield 对应一个 `ModelAttempt`。
3. `ModelAttempt.outcome="response_received"` 仅表示模型尝试拿到可供后续处理的响应；JSON 解析结果仍由
   `CallTrace.final_outcome` 表达。
4. prescan skip 是 `RouteDecision`，不是 `ModelAttempt` 或 `TransportAttempt`。
5. 未知异常不做 blanket wrap；原始异常类型及 `__cause__` 链保持兼容。
6. 旧字段只做加法扩展，不改名、不删除、不改变既有值域。
7. `LLMStats.total_calls` 保持 v0.6.0 的既有 response/error 记账行为；它可能在 fallback、
   self-correction 或终态错误路径中对一个逻辑调用累计多次。

## 3. 阶段二的进入条件

实施开始前需同时满足：

- [ ] AI Information Processor 已按第 10 节完成消费端审查，并明确必需字段、存储映射和隐私要求；
- [ ] 本文第 5 节的待确认决策已关闭，或明确延期到 2B；
- [ ] 确认阶段二不修改 retry 次数、退避算法、timeout、fallback 选择及生产模型配置；
- [ ] trace 数据保留策略允许新增低基数 transport 元数据，但不允许原始正文/异常落库；
- [ ] v0.6.0 的现有三项门禁保持绿色：pytest、ruff、mypy。

若消费端只需要 transport 次数和稳定错误分类，可直接进入 2A；2B 不构成 2A 的阻塞条件。

## 4. 推荐拆分

阶段二分成两个可独立发布、可独立回滚的增量：

### 2A：Transport facts（建议下一版本）

新增每次 transport 调用的结构化事实，解决“重试不可见”。只观测现有行为，不改变现有行为。

### 2B：Terminal consumption APIs（2A 稳定后）

新增安全错误摘要、终态 `on_trace` hook 和无歧义的新统计口径。2B 需再次接受消费端契约审查，因为它
同时涉及隐私、回调失败语义和指标迁移。

不建议把 2A/2B 合成一个发布：transport 采集、错误脱敏、回调生命周期和统计聚合是四种不同风险。

## 5. 公共契约提案与待确认决策

### 5.1 `TransportAttempt`（2A，推荐方案）

```python
@dataclass(frozen=True, slots=True)
class TransportAttempt:
    ordinal: int
    outcome: str
    error_kind: str | None = None
    http_status: int | None = None
    latency_ms: int = 0
    retry_scheduled: bool = False
    backoff_ms: int | None = None
```

字段语义：

| 字段 | 约束 | 含义 |
|---|---|---|
| `ordinal` | 从 1 开始；在一个 `ModelAttempt` 内递增 | transport callable 的调用序号 |
| `outcome` | `success` / `error` | callable 是否正常返回，不代表 JSON 解析或整个逻辑调用成功 |
| `error_kind` | 复用稳定分类；成功为 `None` | 禁止写原始异常类名或异常文本 |
| `http_status` | 可空整数 | 从 HTTP 响应或异常 cause 链提取；无响应时为 `None` |
| `latency_ms` | 非负整数 | 仅本次 callable 的耗时，不含后续 sleep |
| `retry_scheduled` | 布尔值 | retry 层是否在本次失败后进入了现有 sleep 分支 |
| `backoff_ms` | 可空非负整数 | 可安全表示的 requested delay；不是终态判断依据 |

推荐将它嵌套到所属 `ModelAttempt`：

```python
@dataclass(frozen=True, slots=True)
class ModelAttempt:
    # v0.6.0 既有字段保持原顺序与语义
    transport_attempts: tuple[TransportAttempt, ...] = ()
```

选择嵌套而不是放在 `CallTrace` 顶层，原因是 transport retry 必须能无歧义归属到具体模型、JSON 模式和
trigger；消费端无需用额外 ID 做 join。旧代码构造 `ModelAttempt` 时不传新字段仍然有效。

`ModelAttempt.to_dict()` 不再依赖 `dataclasses.asdict()` 递归处理新增 tuple，而要显式把
`transport_attempts` 输出为 JSON array：

```python
"transport_attempts": [attempt.to_dict() for attempt in self.transport_attempts]
```

因此 dataclass 内部保持 immutable tuple，序列化契约固定为 `list[dict[str, JSONScalar]]`。

待消费端确认：

- `outcome=success/error` 是否足够，还是确实需要区分 DNS、连接、读取、解码等更细粒度阶段；
- `http_status` 对成功 attempt 是否必须存在。当前 `_request()` 返回解码后的字典，若强制记录成功状态，
  需要让内部调用额外携带 response metadata；这不应改变公共返回值；
- `retry_scheduled`/`backoff_ms` 是否用于告警或成本分析。若消费端不用，可以仍保留，因为它们是低风险、
  低基数事实；
- 是否需要 `retry_source`（`retry_after` / `exponential_backoff`）。默认不加，避免暴露实现细节成为契约。

### 5.2 采集边界（2A，推荐方案）

每次进入 `async_retry_call` / `sync_retry_call` 所包装的 callable 时创建一条 attempt：

- callable 正常返回：`outcome=success`；
- callable 抛错：retry 决策仍由现有 `classify_error()` 完成；观测字段由下述独立闭集 helper 生成；
- 进入现有 sleep 分支：`retry_scheduled=True`，并尽力记录 `backoff_ms`；
- 达到最大次数、检查时已超过 total timeout 或 no-retry 错误：`retry_scheduled=False` 且
  `backoff_ms=None`；
- sleep 本身不创建 attempt；模型 fallback、schema downgrade 和 self-correction 会创建新的
  `ModelAttempt`，其 `ordinal` 从 1 重新开始。

recorder 的生命周期必须与 generator 边界对齐：

1. shared orchestrator 在**每次 yield 前**通过 `_CallTraceBuilder` 创建一个独立 mutable transport buffer；
   builder 同时把 call-scoped 100 条共享预算和本次是否仍有 model slot 绑定到该 buffer；
2. buffer 作为 `_ChatRequest` 的新增包内字段随本次 request 交给 async/sync driver；
3. `_single_chat` 把同一 buffer 传给 retry 层，所有 transport 调用只写入该实例；
4. generator 收到对应 `_ChatResponse` 后，无论成功或失败，都把 buffer 一次性冻结并随该
   `ModelAttempt` 提交；
5. schema downgrade、self-correction 和 content fallback 的下一次 yield 必须创建新 buffer，ordinal
   从 1 重启；buffer 不得复用或跨 request 串联。

buffer 必须在录制阶段保持有界：每个 event 构造成功后尝试占用 call-scoped transport 预算；有额度才追加到
有序前缀，无额度则只实时增加 `dropped_count`。若创建时已经没有 model slot，buffer 不保留 transport，只
逐次累计 dropped。禁止先无界收集全部 retry、等提交 `ModelAttempt` 时才裁剪。

recorder/observer 是包内可选协议，不成为公共 API。它必须是 total/non-throwing：记录成功事件或错误事件时
若 recorder 自身失败，只发 warning、禁用该实例并继续原业务控制流；不得改变 retry 次数、返回值、异常
类型或 cause 链。未提供 recorder 时 retry 函数保持当前行为和调用方式。async/sync 使用同一个事件构造与
分类 helper，只保留 await/sleep 差异。

禁用 recorder 不能静默伪装成完整 trace。buffer wrapper 自身保留不可抛错的 `dropped_count`：导致 recorder
失败的当前 event 计 1；禁用后的每次 transport 调用仍经过 wrapper 并各计 1。buffer freeze 时把该计数交给
`_CallTraceBuilder`，使最终 `truncated=True` 且 `dropped_events` 增加。warning 或计数失败不得影响业务。

transport 观测分类使用新的私有纯函数，输出只能属于以下闭集：

| retry 决策/原始异常 | transport `error_kind` |
|---|---|
| `ContentPolicyError` 决策 | `content_policy` |
| `TimeoutError` 决策 | `timeout` |
| `TruncationError` 决策 | `json_parse` |
| HTTP 400 | `invalid_request` |
| HTTP 401 | `authentication` |
| HTTP 403 | `permission_denied` |
| HTTP 404 | `model_not_found` |
| HTTP 429 | `rate_limited` |
| HTTP 5xx | `upstream_server_error` |
| `httpx.NetworkError` | `network_error` |
| 其他状态/异常 | `unknown` |

helper 接收本轮已由 `classify_error(original_exception)` 得出的 retry decision 和原始异常，只读取异常类型与
HTTP status；不得信任任意 `LLMCallError.error_kind` 或 provider 字符串。格式 capability 的 400 在 transport
层仍是 `invalid_request`，随后由现有 model orchestration 把 `ModelAttempt.error_kind` 收紧为
`unsupported_response_format`，两层含义不得混用。

latency 计时顺序固定为：每次调用 callable 前取 `attempt_started`；callable 正常返回或抛错后的第一条观测
语句立即取 `attempt_ended`；随后才允许分类、读取 status、计算 backoff 或调用 recorder。`latency_ms` 使用
`max(0, floor((attempt_ended - attempt_started) * 1000))`，不包含任何分类/序列化成本。

`backoff_ms` 从现有实现经过 `min(delay, remaining)` 后传给 sleep 的 float 旁路计算，不得修改原 float：

- 有限且非负：使用共享 helper 按 `floor(delay * 1000)` 转为整数毫秒；
- 负数、NaN 或 infinity：`backoff_ms=None`，但只要进入 sleep 分支仍记 `retry_scheduled=True`；
- 未进入 sleep 分支：`retry_scheduled=False` 且 `backoff_ms=None`。

它不是“实际睡眠完成时长”，也不表示严格 deadline。v0.6.0 会在 callable 失败后才检查 total timeout，并在
截断 sleep 后继续下一次 callable；首个模型与 fallback 的 remaining-timeout 传参也不同。2A 必须原样观测
这些现有语义，禁止为了让 delay 可序列化而改写传给 sleep 的值、增加 sleep 后 deadline 检查或减少调用。

### 5.3 截断策略（2A，推荐方案）

- 保留 v0.6.0 的最多 100 条 `ModelAttempt` 限制；
- 每个逻辑调用最多保留 100 条 `TransportAttempt`，跨所有模型尝试共享额度；
- 超额事件不写入 trace，只增加现有 `CallTrace.dropped_events`；
- 只要丢弃任意模型或 transport 事件，`CallTrace.truncated=True`；
- route decision 继续完整保留，阶段二不改变其现有行为。

提交与计数顺序固定如下：

1. buffer 创建时先判断 model cap。若本条 model 已超出 100 条，则不允许它占用 transport 全局额度；
2. 若 model 可保留，buffer 在录制时从共享 transport 预算保留有序前缀，每个未保留 transport 实时增加
   `dropped_count`；
3. model 超 cap 时提交 `dropped_events += 1 + buffer.dropped_count`；model 可保留时提交
   `dropped_events += buffer.dropped_count`；
4. 最后提交含已保留 transport tuple 的 `ModelAttempt`；只有实际嵌套进已记录 model 的 transport 才算
   recorded，才进入后续统计。

这会把 `dropped_events` 的含义从“被丢弃的模型事件”扩展为“被丢弃的 model 或 transport trace event”。
字段类型和旧值保持兼容，但文档必须同步说明语义扩展。

### 5.4 安全错误摘要（2B，推荐方案）

2B 候选是在 `LLMCallError` 上增加一个可选字段：

```python
safe_error_code: str | None = None
```

规则：

- 只读取经单独 ADR 批准的结构化上游 code/type JSON path；
- `(provider, JSON path, exact upstream value)` 必须精确命中显式映射，并转成库拥有的 canonical code；
- 未知值即使满足字符格式也必须返回 `None`，禁止直接透传 provider-controlled 字符串；
- canonical code 仍限制为 `[A-Za-z0-9._:-]` 且最长 64 字符；
- 禁止回退到 `str(exception)`、自由文本 message、完整响应正文、headers、payload、prompt 或 cause 对象；
- `TransportAttempt` 初版不带 message，避免每次 retry 重复保存可能敏感且高基数的文本；
- 既有异常的 `str(error)` 不改，安全字段是供日志/落库显式选择的替代品。

当前批准映射表为空，因此**不得实施或发布该字段**。AI Information Processor 审查需要提供真实、已脱敏的
样本需求；2B-1 开始前必须新增 ADR，逐行冻结：

| provider | JSON path | exact upstream value | canonical code | 消费用途 |
|---|---|---|---|---|
| （尚无批准项） | | | | |

解析优先级固定为映射表中的 JSON path 顺序，首个命中项获胜。code/type 冲突时不自行推断，仍按该顺序；
response 非 JSON、不是 object、path 缺失、值不是 string、值未知或存在解析异常时均返回 `None`。如果消费端
无法提供足以批准的映射，2B 省略 `safe_error_code`，继续只使用库已有的 `error_kind`。

`safe_error_message` 默认延期。任意上游 `error.message` 都可能回显 prompt、PII 或未知 credential，经过
best-effort redaction 后仍不能宣称 safe。若消费端证明必须提供 message，只允许根据已知 provider/code
映射到库内固定模板，禁止持久化任何上游自由文本。

### 5.5 `on_trace`（2B，推荐方案）

客户端构造参数新增：

```python
on_trace: Callable[[CallTrace], None] | None = None
```

语义：

- 对产生稳定 `CallTrace` 的逻辑调用，在 trace freeze 后恰好调用一次；
- 成功与 `LLMCallError` 终态都调用；`SkipRequestError` 和未包装未知异常若没有稳定 trace，则不调用；
- callback 接收的就是返回值/异常上同一个不可变 trace；
- callback 抛错只记内部 warning，不改变原调用成功/失败结果；
- 首版只支持同步 callback。async callback、批量发送、持久化队列和重试由消费端负责；
- 默认 `None`，因此现有用户没有行为变化。

调用顺序固定为：成功时先把 trace 挂到 result，再调用 `on_trace`，最后保持既有 `on_success`；失败时先把
trace 挂到 `LLMCallError`，再调用 `on_trace`，最后保持既有 `on_error` 并重抛。实现应使用一个终态 helper
供 `chat()`/`chat_json()` 与 async/sync 复用，避免多个 freeze 分支各自调用导致重复。callback 失败被隔离后
仍继续既有 success/error hook。

待消费端确认：是否要由库提供 hook，还是 AI Information Processor 直接在现有成功/异常边界读取 trace。
如果消费端已有统一 wrapper，后者更简单，2B 可不增加 `on_trace`。

### 5.6 新统计口径（2B，推荐方案）

旧 `LLMStats.total_calls`、`success_count`、`error_count` 的 v0.6.0 response/error 记账行为保持不变；不得
把它们重新解释为逻辑调用口径。新增名字明确的计数：

- `logical_call_count`：冻结的逻辑调用 trace 数；
- `model_attempt_count`：被记录的 `ModelAttempt` 数；
- `transport_attempt_count`：被记录的 `TransportAttempt` 数；
- `transport_retry_count`：`retry_scheduled=True` 的失败 transport attempt 数；
- `trace_truncated_count`：`truncated=True` 的逻辑调用数。

所有新增计数从最终冻结 trace 一次性聚合，禁止在多层控制流中分别递增，避免重复统计。因当前 `LLMStats`
是进程内可变对象且无并发保证，阶段二不承诺新增线程安全语义。

待消费端确认：这些进程内统计是否仍有价值。若生产指标完全由 trace 消费端聚合，建议只保留 trace 契约，
不扩大 `LLMStats`。

## 6. TDD 实施顺序

每个任务先写失败测试，确认失败原因指向缺失契约，再写最小实现；对应测试与三项门禁通过后及时 commit。

### T2A-1：纯数据契约

先写测试：

- `TransportAttempt` frozen、slots、`to_dict()` 序列化稳定；
- `ModelAttempt.transport_attempts` 默认为空 tuple；
- v0.6.0 位置/关键字构造方式仍可工作；
- 包根导出 `TransportAttempt`；
- `ModelAttempt.to_dict()["transport_attempts"]` 精确为 list，且 `json.dumps(trace.to_dict())` 成功；
- v0.6.0 与 2A payload 的滚动升级嵌套形态均被锁定；
- trace 中不出现原始异常、正文、header、payload 或 credential。

再实现 `_trace.py`、`__init__.py` 的最小加法变更。

### T2A-2：retry recorder 单元契约

先对 async/sync 各写完全对称的测试：

- 首次成功：1 条 success，ordinal=1，无 backoff；
- 失败后成功：error + success，两条 latency，首条有 backoff；
- max retries 耗尽：每次调用各一条，末条无 backoff；
- fatal/no-retry、timeout、network、429/5xx 的稳定分类；
- `Retry-After` 与计算 backoff 按共享 floor 规则旁路转成毫秒，不修改传给 sleep 的 float；
- 覆盖正小数、sub-millisecond、负数、NaN、infinity，并验证无法表示时
  `retry_scheduled=True/backoff_ms=None`；
- recorder 自身为空时，现有 retry 测试与异常链完全不变；
- 用 fake monotonic/sleep 锁定失败→截断 sleep→仍执行下一次 callable 的现有行为；
- 用会在分类 helper 中继续推进的 fake monotonic 锁定 callable 返回/抛错后立即截取结束时间，确保分类成本
  不进入 async/sync `latency_ms`；
- 锁定首个模型与 fallback 的 timeout 参数，以及 `Retry-After` 大于 remaining 时的截断值；
- recorder 在记录 success 或处理原异常时失败，都只被禁用且不掩盖业务结果/cause 链；当前及后续未记录
  event 逐条计入 buffer `dropped_count`；
- transport 分类闭集覆盖 raw content-policy HTTP、truncation pattern、未知状态、网络/timeout、恶意自定义
  `LLMCallError.error_kind`，且 retry 决策行为不变。

### T2A-3：嵌套归属与端到端矩阵

先写覆盖以下场景的集成测试：

- 普通成功：1 model / 1 transport；
- 429 后成功：1 model / 2 transport；
- schema downgrade：2 model，每个 transport ordinal 独立；
- content fallback：transport 归属正确的 primary/fallback model；
- self-correction：每次新 `ModelAttempt` 分别持有自己的 transport；
- 每个 `_ChatRequest` 恰好绑定一个 recorder，且相邻 request 的 recorder identity 不同；
- prescan skip：被跳过的模型仍无 model/transport attempt；
- terminal error：异常 trace 含完整已记录 transport facts 且 cause 链不变；
- recorder 在 success/error 路径禁用后，最终 trace 均正确标记 `truncated` 并累计缺失 event；
- async/sync 对同一脚本生成等价结构；
- 超过 100 条 transport event 后正确截断并累计 `dropped_events`；
- 单个 request 配置超大 `max_retries` 时，buffer 长度始终不超过共享剩余额度，超额只增长计数；
- 同时越过 model/transport cap、以及被丢弃 model 含多次 retry 时符合第 5.3 节公式；
- JSON parse 成败不改变 transport success 的定义。

再把包内 recorder 从 shared orchestrator 贯穿到 async/sync `_single_chat` 和 retry 层。

### T2A-4：文档与发布

更新 README、integration guide、decision log、版本与 changelog（若仓库有），明确三层计数：logical call、
model attempt、transport attempt。运行全量门禁和独立 review 后发布 2A。

### T2B-1：安全错误摘要

先基于消费端证据编写并批准映射 ADR；没有非空批准表就取消本任务，不新增公共字段。然后用每条正向映射
fixture 与攻击性反例写测试：code/type 冲突、伪造 code、自由文本 message、`sk-*` key、JWT、UUID/query
token、纯数字标识、PII、超长字符串、嵌套 JSON、非 object/非 JSON 正文、缺失 path、非 string 值、恶意
对象 `__str__`。要求只有精确 `(provider, path, value)` 能映射为 canonical `safe_error_code`，其他一律
`None`；本任务不实现自由文本脱敏或 `safe_error_message`。

### T2B-2：终态 hook

先写 success/error/exactly-once/callback-raises/unknown-exception/async-sync parity 测试，再接入统一 freeze 边界。

### T2B-3：统计迁移

先锁定 refusal fallback、self-correction、parse exhaustion 下旧字段的现有累计结果，再对五个新计数写
trace 驱动测试；实现只从 frozen trace 聚合一次，并验证 `reset()` 清零全部新增字段。

## 7. 预计修改面

| 文件 | 2A | 2B |
|---|---|---|
| `src/llm_compat/_trace.py` | `TransportAttempt`、嵌套 builder、cap | 无或小幅辅助 |
| `src/llm_compat/retry.py` | 内部 recorder、共享事件 helper | 无 |
| `src/llm_compat/_base.py` | 建立并冻结每个模型尝试的 transport recorder | 统一终态 hook/统计入口 |
| `src/llm_compat/client.py` | async recorder 贯穿 | 客户端 hook 参数 |
| `src/llm_compat/sync.py` | sync recorder 贯穿 | 客户端 hook 参数 |
| `src/llm_compat/errors.py` | 复用稳定分类 | 安全字段；提取逻辑建议放独立私有模块 |
| `src/llm_compat/_types.py` | 无 | 新统计字段 |
| `src/llm_compat/__init__.py` | 公共导出 | 如有新增公共类型则导出 |
| `tests/` | contract/retry/integration parity | redaction/hook/stats |
| `README.md`、`docs/` | 2A 契约 | 2B 消费与隐私说明 |

具体行号不写进计划，避免实现前代码漂移造成错误锚点。

## 8. 明确不在阶段二范围内

- 改变 max retries、退避算法、timeout 或总 deadline 行为；
- 改变 content fallback、availability fallback、schema downgrade 或 self-correction 决策；
- 修复重复/循环 fallback 配置；
- `chat_stream` 完整 trace；
- 记录 prompt、messages、payload、响应正文、headers、API key 或原始异常；
- 高基数 span/OTel exporter、后台队列、网络上报或数据库 schema 管理；
- AI Information Processor 的生产迁移和历史数据回填；
- 修改生产 fallback 模型或部署配置。

消费端审查若发现上述事项确实是前置条件，应单列后续任务，不在阶段二实现中顺手扩 scope。

## 9. 兼容、回滚与验收门禁

### 9.1 兼容要求

- 所有新增 dataclass 字段必须有默认值并追加在既有字段之后；
- `to_dict()` 允许新增 key，但既有 key/value 语义不变；
- 不提供 recorder/hook 时，现有调用路径、异常类型、cause 链、sleep 和返回值不变；
- `LLMStats.total_calls` 继续保持现有 response/error 记账行为，不宣称是逻辑调用口径；
- 新 trace 仍仅含 JSON-safe primitives、list/dict，且 dataclass 本体不可变；
- 2A/2B 均可由消费端忽略新字段完成滚动升级。

### 9.2 回滚策略

2A 与 2B 分开发版。回滚生产者版本时，消费端必须把新增字段视为可选；回滚消费端时，旧消费者应忽略
未知 JSON key。任何 consumer schema 若拒绝未知字段，必须在 2A 发布前修复。

### 9.3 每个实现 commit 的门禁

```bash
uv run pytest <本任务相关测试>
uv run ruff check src tests
uv run mypy src
```

### 9.4 阶段完成门禁

```bash
uv run pytest
uv run ruff check src tests
uv run mypy src
```

先完成并提交全部代码、README、integration guide、decision log、session handoff、版本元数据和 lockfile
变更，再运行最终全量三项门禁，然后开始独立 Codex gate Review。每轮都基于当时完整最新 diff 和测试结果；
发现实质性意见后修复、补回归测试、同步文档并重新运行全量门禁，清洁计数归零。只有**连续 2 轮没有新增
实质性意见**才算该阶段完成；两轮期间或之后任何 tracked-file 变化都必须重置计数并重新审查。最终 gate
达成后只允许创建不改变树内容的提交/tag；若还需改文件，必须重新走门禁。

## 10. AI Information Processor 审查清单

请在消费仓库内结合真实写入路径回答，不能只评审类型命名：

1. 当前从 `ChatResult.trace` 与 `LLMCallError.trace` 读取数据的入口分别在哪里？是否存在遗漏终态？
2. 当前 ledger/event schema 是否允许 `model_attempts[].transport_attempts[]` 这一嵌套数组和未知字段？
3. 是否必须做数据库迁移、索引或 dashboard 变更？请列出字段映射和基数风险。
4. 消费端真正需要的是每次 transport 事实、聚合计数，还是两者都要？
5. `success/error`、`error_kind`、`http_status`、`latency_ms`、`retry_scheduled`、`backoff_ms` 是否足以定位问题？
6. 是否需要成功 attempt 的 HTTP status？若需要，请说明具体查询/告警场景。
7. 是否需要 `safe_error_message`？仅有 `error_kind` + `safe_error_code` 是否已足够？若需要
   `safe_error_code`，请提供所需 provider/path/exact value/canonical code 与具体消费用途；
8. 消费端已有统一 success/error wrapper 时，`on_trace` 是否仍有必要？
9. exactly-once 指“库内每个逻辑调用调用一次 hook”，还是消费端要求端到端 exactly-once 持久化？后者不由
   进程内 callback 保证。
10. trace 截断时，消费端如何告警和展示 `dropped_events`？100 条 transport 上限是否足够？
11. 消费端是否错误地把 transport error、model error、JSON parse error 合并到同一失败指标？
12. 滚动升级期间缺少新字段的 v0.6.0 事件能否继续正常处理？

审查输出请按以下结构返回：

- `BLOCKER`：不解决就不能进入 2A；
- `SHOULD CHANGE`：建议调整契约或实施顺序；
- `CAN DEFER`：可留到 2B/后续；
- `FIELD MAPPING`：生产者字段到消费端表/事件/指标的映射；
- `VERDICT`：`READY FOR 2A` 或 `NOT READY`，并列出理由。

## 11. 可直接交给 Claude Code 的审查 Prompt

```text
请在 AI Information Processor 仓库内做一次只读的跨仓库契约审查，不要修改代码。

生产者计划文档：
/home/zlx/projects/personal/llm-compat/docs/sessions/260712-llm-compat-observability-phase2/PHASE2_PLAN.md

请完整阅读该文档，然后在当前消费仓库中定位 ChatResult.trace、LLMCallError.trace、ledger/event
写入、数据库 schema、指标和 dashboard 的真实消费路径。逐项回答文档第 10 节的 12 个问题，重点验证：

1. TransportAttempt 的嵌套结构和字段是否满足实际查询；
2. v0.6.0 → 2A 滚动升级是否会被严格 schema/未知字段拒绝；
3. safe_error_message 与 on_trace 是否确有必要；
4. logical/model/transport 三层统计是否会被混用；
5. 是否存在隐私、高基数、重复写入或错误 exactly-once 假设。

输出必须分为 BLOCKER、SHOULD CHANGE、CAN DEFER、FIELD MAPPING、VERDICT 五部分。
每条意见给出消费仓库的文件路径和行号证据。只有能改变契约、正确性、安全性、兼容性或实施顺序的
意见才算实质性意见；不要把纯风格偏好列为 blocker。最终明确给出 READY FOR 2A 或 NOT READY。
```

## 12. 当前推荐结论

在消费端审查前，推荐暂定：

- 2A 采用嵌套 `TransportAttempt`，字段为
  ordinal/outcome/error_kind/http_status/latency/retry_scheduled/backoff；
- recorder 保持包内可选协议，retry 行为零变化；
- 使用每逻辑调用 100 条 transport event 上限，并复用 `truncated`/`dropped_events`；
- 2B 只有在消费端提供并批准非空映射 ADR 后才做 `safe_error_code`，否则省略；
  `safe_error_message` 延期，`on_trace`、新增 `LLMStats` 是否实施由真实消费路径决定；
- 不满足第 3 节进入条件时，不开始代码实施。

这份结论是待消费端验证的生产者建议，不是已经冻结的最终公共契约。

## 13. AI Information Processor 消费端 Review 结论（2026-07-12）

### 13.1 总体判断

**VERDICT：`NOT READY`。不建议按本文当前范围直接启动完整 Phase 2。**

v0.6.0 已经解决了此前最核心的消费端问题：敏感词预检或 content fallback 后的上游失败，不再只能以
`None` 传给下游并被误记为 `json_parse_failed`。`RouteDecision`、`ModelAttempt`、终态
`final_outcome` 与稳定的 `LLMCallError` 已足以表达该问题的模型级事实。

`TransportAttempt` 解决的是下一层问题，即同一个模型尝试内部的实际 transport 调用次数、重试原因和
退避耗时。这个方向有长期价值，但当前消费端尚未建立成功 trace 的持久化和查询闭环，因此直接实现完整
2A 会产生“生产者已经生成、消费端仍然看不到”的数据。2B 当前没有得到真实消费需求支持，应全部延期。

推荐先完成消费端 v0.6.0 闭环和独立的 retry 正确性/日志隐私修复，再根据实际数据决定是否实施缩小后的
2A-lite。

### 13.2 BLOCKER

以下事项不解决，不应进入当前 2A 实施。

#### B1. 消费端只持久化失败 trace，2A 的主要成功恢复场景会继续丢失

AI Information Processor 已在：

- `src/processing/ai_client.py:265-312` 从成功 `ChatResult.trace` 提取 `call_trace`；
- `src/processing/ai_client.py:342-361` 从稳定 `LLMCallError.trace` 提取失败 trace。

但 `src/processing/ai_ledger.py:242-255` 只有在 `status == "failed"` 时，才把 trace 写入
`result_json.failure.trace`。因此以下最需要 transport facts 的场景仍不会落账：

- 429 或 5xx 后重试成功；
- network error 后重试成功；
- schema downgrade 后成功；
- content fallback 后成功。

进入 2A 前，消费端必须先决定成功和失败共用的中性存储位置。推荐：

```text
article_ai_runs.result_json.observability.trace
```

不建议继续把公共 trace 放在 `failure.trace` 下。若出于存储量考虑不保存完整成功 trace，也必须先冻结并实现
成功路径的聚合字段，否则 2A 无法完成其主要目标。

#### B2. 当前消费端丢弃了 `dropped_events`，无法判断 trace 完整性

`src/processing/ai_client.py:294-312` 的 `_trace_summary()` 保存了 `truncated`，但没有保存
`dropped_events`。即使 2A 发布，消费端也只能知道 trace 被截断，无法知道丢失数量。

消费端在进入 2A 前至少应保存 v0.6.0 已有的 `dropped_events`，并为缺字段的旧事件提供默认值。

#### B3. 不得把当前不严格的 total-timeout 行为固化为 Phase 2 公共兼容要求

当前 `src/llm_compat/retry.py` 会在一次失败后把 sleep 截断到 remaining timeout，但 sleep 返回后不会再次
检查 deadline，而是可能继续执行下一次 callable。本文第 5.2 节和 T2A-2 当前要求用测试锁定“截断 sleep
后仍执行下一次 callable”的行为。

该行为可以暂时不在 2A 中顺手修改，但不应被升级为长期不变契约。进入 2A 前应单列 retry deadline 任务，
明确 `total_timeout` 是 soft timeout 还是 hard deadline；2A 只观测最终批准的语义。若 deadline 修复暂时
延期，相关测试只能作为现状 characterization，并明确允许后续修正，不能作为兼容门禁。

### 13.3 SHOULD CHANGE

#### S1. 将完整 2A 缩小为按真实查询需求驱动的 2A-lite

建议首版保留：

```python
@dataclass(frozen=True, slots=True)
class TransportAttempt:
    ordinal: int
    outcome: str
    error_kind: str | None = None
    http_status: int | None = None
    latency_ms: int = 0
    retry_scheduled: bool = False
```

其中：

- `outcome` 保持 `success/error` 即可；
- DNS、connect、read 等细分继续通过稳定 `error_kind` 表达，不扩张 `outcome` 值域；
- 成功 attempt 不要求 `http_status`，避免仅为记录通常无诊断价值的 2xx 而改变 `_request()` 内部返回形态；
- `backoff_ms` 只有在消费端已经定义退避延迟查询或告警后才加入首版，否则延期；
- `retry_source` 延期。

#### S2. transport 分类必须增加响应 envelope 解码失败

当前 async/sync `_request()` 的 callable 同时包含 HTTP 请求、`raise_for_status()` 和 `response.json()`。
因此 HTTP 2xx 但响应 envelope 不是合法 JSON 时，transport callable 会抛错；现有提案会将它归为
`unknown`。

建议稳定闭集增加：

```text
response_decode
```

它表示 OpenAI-compatible HTTP response envelope 无法解码，不能使用 `json_parse`；后者已用于模型内容或
业务 JSON/self-correction 解析失败。两层必须保持可区分。

#### S3. 不要扩展旧 `CallTrace.dropped_events` 的既有语义

本文第 5.3 节准备把 `dropped_events` 从“被丢弃的 model event 数”扩展为“被丢弃的 model 或 transport
event 数”。这与第 2.1 节“旧字段不改变既有语义”的要求冲突，也让消费端无法判断哪个嵌套
`ModelAttempt.transport_attempts` 不完整。

建议保留旧字段语义，并做加法扩展：

```python
class CallTrace:
    dropped_events: int = 0  # 保持 v0.6.0 语义
    dropped_transport_attempts: int = 0

class ModelAttempt:
    transport_attempts: tuple[TransportAttempt, ...] = ()
    transport_attempts_truncated: bool = False
    dropped_transport_attempts: int = 0
```

若担心字段重复，至少也应保留 model 级完整性标志；仅有 call 级混合计数不足以解释嵌套数组。

#### S4. 降低 recorder 防御性设计与测试矩阵的实施比重

recorder 不得改变 retry 的返回值、次数和异常链，这一原则正确；但当前计划为 recorder 禁用、计数器再次
失败、NaN/infinity backoff、恶意 `error_kind` 等场景设计了接近核心路径规模的协议和测试。

建议先锁定以下核心门禁：

- 首次成功；
- retry 后成功；
- retry 耗尽；
- fatal/no-retry；
- 429/5xx/network/timeout/response_decode；
- async/sync parity；
- schema downgrade、fallback、self-correction 的正确归属；
- recorder 失败不改变业务结果，并明确标记当前 ModelAttempt 不完整；
- 有界收集。

其他数值异常和攻击性对象测试可在核心契约稳定后补充，不应阻塞首个可验证增量。

#### S5. 隐私工作应先处理现有自由文本异常日志

trace 不保存原始异常是正确的，但当前 `src/llm_compat/retry.py` 的 retry warning 仍直接格式化 `exc`，消费端
`src/processing/ai_client.py` 也存在记录异常文本的路径。provider message 可能包含请求片段、标识符或其他
高基数信息。

在设计 `safe_error_code` 前，应先单列日志隐私修复：生产日志默认只输出稳定 `error_kind`、HTTP status、
model、attempt ordinal 和 request ID；原始异常文本只能进入明确受控的诊断通道。否则 trace 的严格隐私边界
不能解决整体可观测链路的泄露风险。

### 13.4 CAN DEFER

以下内容不构成 2A-lite 前置条件，当前消费端也没有足够证据支持实施：

- `safe_error_code`：当前没有已批准的 provider/path/value 映射和具体查询用途；
- `safe_error_message`：继续延期，不持久化任意上游自由文本；
- `on_trace`：AI Information Processor 已在统一 `_do_chat()` 成功/异常边界读取 trace，不需要新增库级 hook；
- 新 `LLMStats`：生产事实应从 append-only `article_ai_runs` 或后续 trace 指标聚合，避免扩大无并发保证的
  进程内统计；
- 成功 attempt 的 HTTP status；
- `backoff_ms` 与 `retry_source`，除非先定义实际报表/告警；
- stream trace、OTel exporter、后台上报与历史回填。

### 13.5 FIELD MAPPING

当前数据库不需要新增列。`ArticleAIRun.result_json` 是 JSON 字段，能够容纳新增嵌套数组；没有严格 Pydantic
schema 或未知字段拒绝路径，因此 v0.6.0 → 2A 的滚动升级可通过“缺字段即默认空 tuple/零值”兼容。

推荐映射如下：

| 生产者字段 | 消费端位置 | 用途与约束 |
|---|---|---|
| `CallTrace.requested_model` | `result_json.observability.trace.requested_model` | 请求模型 |
| `CallTrace.final_model` | `result_json.observability.trace.final_model` | 实际终态模型 |
| `CallTrace.final_outcome` | `result_json.observability.trace.final_outcome` | 逻辑调用终态；失败记录的 `reason` 可继续保留为便捷聚合字段 |
| `CallTrace.route_decisions` | `result_json.observability.trace.route_decisions` | prescan/fallback 路由事实 |
| `CallTrace.model_attempts` | `result_json.observability.trace.model_attempts` | 模型/格式尝试链 |
| `ModelAttempt.transport_attempts` | `result_json.observability.trace.model_attempts[].transport_attempts` | transport 明细；旧事件缺失时视为空且标记版本未知，不解释为“确定没有 retry” |
| `CallTrace.truncated` | `result_json.observability.trace.truncated` | trace 完整性告警 |
| `CallTrace.dropped_events` | `result_json.observability.trace.dropped_events` | v0.6.0 model event 丢弃数，保持旧语义 |
| 新 transport 截断字段 | 同层或所属 `model_attempts[]` | transport 完整性，必须与旧 `dropped_events` 分开 |
| `TransportAttempt.error_kind` | 不单独建索引，先从 JSON 聚合 | 低基数失败分类 |
| `TransportAttempt.http_status` | 不单独建索引，先从 JSON 聚合 | 仅错误 attempt 有实际诊断价值 |
| `TransportAttempt.latency_ms` | 不单独建索引，先从 JSON 聚合 | retry 延迟分析 |
| `TransportAttempt.retry_scheduled` | 不单独建索引，先从 JSON 聚合 | retry 发生率与恢复率 |

当前不建议为 transport 字段新增数据库列或索引。先验证查询频率和 JSON 扫描成本；只有形成稳定 dashboard
或告警后，再考虑生成列、独立事件表或指标管道。`request_id` 与时间戳属于高基数维度，如后续保存，只用于
单次诊断，不作为 metrics label。

### 13.6 调整后的推荐执行顺序

1. **消费端 v0.6.0 闭环**：成功/失败共用中性 trace 存储位置，补存 `dropped_events`，增加最小查询或报告，
   验证现有模型级事实是否已满足生产诊断。
2. **独立正确性与隐私任务**：明确并修复 retry deadline 语义；停止默认日志直接输出 provider-controlled
   异常文本。不得与 2A 混成同一行为变更发布。
3. **需求门禁**：用真实样本证明至少存在一个 v0.6.0 无法回答的查询，例如 retry 次数、retry 恢复率、
   transport 错误分布或 backoff 延迟贡献。
4. **实施 2A-lite**：采用嵌套结构、稳定分类、局部完整性标志和有界 recorder；不加入未被消费的字段。
5. **重新审查 2B**：2A-lite 稳定并实际消费后，再基于新证据决定是否存在 2B，而不是默认继续实施。

满足上述第 1～3 项、关闭 B1～B3，并按 S1～S5 更新公共契约后，消费端可重新给出
`READY FOR 2A-LITE`。在此之前，不开始 T2A-1 的公共类型实现。
