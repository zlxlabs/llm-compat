# LLM Compat fallback 可观测性方案评审

- 日期：2026-07-12
- 模式：SELECTIVE EXPANSION
- 选择方案：B，结构化调用轨迹
- 状态：DONE

## 结论

问题真实存在：普通 HTTP 失败会在 `llm-compat` 中被包装并抛出，但失败异常没有携带敏感词预检、实际模型、JSON mode 和 fallback 尝试链；消费层把异常转换成 `None` 后，下游会把它误记为 `json_parse_failed`。

消费项目 `fix/llm-failure-observability` 分支已经完成应用层止血，但它通过私有属性和重新执行敏感词检测推断路由，只能作为临时兼容方案，不能替代库级事实记录。

## 已拍板的设计

1. 成功和失败共用同一种 `CallTrace`。成功结果通过 `ChatResult.trace` 暴露，失败通过 `LLMCallError.trace` 暴露；旧的 `fallback_from` 和 `fallback_chain` 暂时保留。
2. 调用轨迹分为三层：路由决策、模型尝试、模型尝试内部的 HTTP/transport 尝试。预检跳过主模型是路由决策，不是假装成一次模型调用。
3. 保留现有具体异常类型。新增 `LLMCallError(LLMError)`，让 `FatalError`、`RetryableError`、`ContentPolicyError`、`JSONParseError` 等继承它；`SkipRequestError` 仍直接继承 `LLMError`。
4. 公共轨迹使用不可变快照；内部编排可以使用 builder，返回或抛出前冻结，避免调用方修改历史事实。
5. `error_kind` 使用稳定语义分类，HTTP 状态单独保存在 `http_status`。不把 `http_400` 当成长期错误分类。
6. 不把所有 HTTP 400 当作 JSON Schema 不兼容。只有上游错误体明确指向 `response_format`、`json_schema` 或 unsupported capability 时，才允许同模型从 `json_schema` 降级为 `json_object` 一次。
7. `content_fallbacks` 继续只表达内容策略 fallback，不悄悄扩大成通用故障转移。429、5xx、网络错误仍先在同模型内重试；是否增加 availability fallback 留给独立设计。
8. `FallbackExhaustedError` 只在所有符合当前 fallback 策略的候选均已实际尝试后使用。它应保留最后一个具体失败作为 `__cause__`，并在 trace 中保留每个候选的原因。
9. trace、日志和 callback 不保存 prompt、请求正文、完整响应正文、headers 或 API key。底层 `httpx` 异常可保留在 `__cause__`，但不得被自动序列化进 trace。
10. 异步和同步客户端必须共享同一套类型与语义；首版不扩展 `chat_stream()`，但公开类型不能阻碍后续加入流式轨迹。

## 建议的数据模型

```text
CallTrace
├── request_id
├── requested_model
├── started_at / latency_ms
├── route_decisions[]
│   └── model, action, reason
├── model_attempts[]
│   ├── model, provider, json_mode, trigger
│   ├── outcome, error_kind, http_status, latency_ms
│   └── transport_attempts[]
│       └── ordinal, outcome, error_kind, http_status,
│           latency_ms, backoff_ms
└── final_outcome / final_model / truncated
```

公共错误建议包含：

```text
LLMCallError
├── error_kind
├── trace
├── safe_error_code
├── safe_error_message
└── __cause__  # 原始异常，不自动序列化
```

推荐的稳定错误类别至少包括：

- `invalid_request`
- `authentication`
- `permission_denied`
- `model_not_found`
- `rate_limited`
- `upstream_server_error`
- `timeout`
- `network_error`
- `content_policy`
- `unsupported_response_format`
- `json_parse`
- `fallback_exhausted`
- `unknown`

错误分类函数不应继续只返回异常 class；建议返回内部 `ErrorInfo`，同时包含分类、HTTP 状态、是否同模型重试、是否切换模型以及安全错误摘要。异常类型和下一步动作不再硬耦合。

## 11 项评审

### 1. 架构

- 调用轨迹的唯一事实源应位于 `_content_fallback_orchestrator` 与 retry 层之间，由编排器创建、各层追加，终态统一冻结。
- 路由决策、模型尝试和 transport 重试必须分层，否则“跳过主模型”和“请求主模型失败”仍会混淆。
- 同一个 trace 同时附着于成功和失败，避免两套观测模型。
- 不在本次把 `content_fallbacks` 扩大成通用路由系统。
- 同步与异步实现必须由共享核心行为驱动，不能分别复制 trace 逻辑。
- 回滚方式：新字段为 additive；旧字段和旧异常捕获方式保留，消费者可以暂不读取 trace。

### 2. 错误与补救路径

| 失败 | 当前行为 | 本次建议 |
|---|---|---|
| 明确内容策略错误 | 换模型 | 保持，记录 route/attempt |
| 普通 HTTP 400 | `FatalError` | `invalid_request`，默认终止 |
| 明确 JSON Schema 不支持 | 改 `json_object` | 保持一次，记录两个 model attempt 或同模型的两个 format attempt |
| 401 | `FatalError` | `authentication`，终止且不 fallback |
| 非内容型 403 | `FatalError` | `permission_denied`，终止 |
| 404 | `FatalError` | `model_not_found`，终止 |
| 429/5xx/网络 | 同模型退避重试 | 保持，记录每次 transport attempt |
| timeout | 当前代码实际不重试 | 本 PR 先准确记录；行为修改拆成独立 PR |
| JSON 解析失败 | 可 self-correction | 保持，只有收到模型响应后才能归为 `json_parse` |
| fallback 全拒绝 | `ContentPolicyError` | 兼容型 `FallbackExhaustedError(ContentPolicyError)` 或等价继承关系 |

发现的现有缺陷：`type(Exception).__name__` 会把错误统计写成 `type`；必须随实现修正为真实异常类别。`TimeoutError`/`TruncationError` 名义上属于 `RetryableError`，却在 `_NO_RETRY_TYPES` 中禁止重试，文档与行为冲突；不要在可观测性 PR 中静默改变生产重试策略。

### 3. 安全与威胁模型

- 不记录 prompt、message content、图片、请求 payload、响应正文或 headers。
- `safe_error_code` 优先从固定 JSON 字段提取，限制长度和字符集。
- `safe_error_message` 最大 256 字符，执行 API key、Bearer token、URL query 和常见 secret 模式脱敏；无法证明安全时返回 `None`。
- trace 序列化不得递归序列化 `__cause__` 或 `httpx.Response`。
- callback/日志处理器异常不得改变 LLM 调用结果。
- provider 和 model 作为日志标签可能产生高基数；metrics 中应使用受控 provider/error_kind，model 仅用于日志或明确白名单指标。

### 4. 数据流与边界情况

- 无 fallback 配置：仍生成主模型 attempt；失败异常有完整 trace。
- fallback 为空或因视觉能力过滤为空：记录 route decision，并返回明确的 `no_eligible_fallback` 上下文，不能伪装成内容拒绝。
- 预检检测器不可用：记录 detector unavailable/未执行，不应写成 `hit=False`。
- 同一个模型在链中重复：配置校验应拒绝或去重，防止循环。
- JSON Schema 降级后消息被注入 schema：trace 只记录 mode，不记录注入后的正文。
- 总超时在模型切换前耗尽：最后错误分类为 timeout/deadline exceeded，trace 保留已完成 attempts。
- trace 达到上限时设置 `truncated=True` 和丢弃计数，禁止静默截断。建议上限 100 个 transport events。
- 时间使用 monotonic 计算耗时；只在顶层保留可序列化的 UTC 开始时间。

### 5. 代码质量

- 新增独立内部模块（例如 `_trace.py`）承载 frozen dataclasses 和 builder，不继续扩大 `_base.py`。
- `_is_format_error()` 和消费项目的异常链遍历应被统一的错误分类替代。
- `classify_error()` 返回 `ErrorInfo`，避免“异常类型同时决定重试动作”的隐式耦合。
- 枚举在公开 JSON 中使用稳定字符串；内部可使用 `StrEnum`，不得暴露实现类名作为长期指标。
- 保留零重依赖约束，只使用 dataclasses、enum 和标准库。

### 6. 测试

- 所有核心契约同时覆盖 async/sync。
- 成功直连、预检跳过后成功、200 拒绝后 fallback 成功。
- 普通 400：实际模型、provider、json mode、状态码和 cause 不丢失。
- 明确 schema unsupported：同模型 schema→object 两次尝试可见。
- generic 400 不误触发 schema 降级。
- 429→503→成功：三次 transport attempts、backoff 和最终成功可见。
- fallback 全拒绝：`FallbackExhaustedError` 且保持 `ContentPolicyError` 捕获兼容。
- 真正的坏 JSON/self-correction 耗尽才是 `json_parse`。
- trace 序列化不包含 prompt、authorization、响应正文或 cause。
- callback 抛异常不影响调用结果。
- trace 截断、空 fallback、视觉过滤、总 deadline 耗尽。
- 公开导出和旧异常继承关系需要契约测试。

### 7. 可观测性与监控

- 增加可选 `on_trace(CallTrace)` 终态 hook，每次逻辑调用只触发一次；异常必须吞掉并告警。
- 本地 `LLMStats` 由 trace 终态更新，修正错误类别统计；不要让每层重复计数。
- 推荐指标：调用结果、错误类别、provider、fallback 原因、格式降级结果、transport 重试次数和耗时。
- 不直接在库里绑定 Prometheus/OpenTelemetry，保持依赖和框架中立。
- 消费项目在升级后直接从 trace 落账，停止读取 `_content_fallbacks` 和 `_get_sensitive_detector()` 私有成员。
- 配套 runbook 至少说明：400 分类、schema 降级、内容拒绝、超时、fallback exhausted 的排查顺序。

### 8. 数据库与状态

- `llm-compat` 不引入数据库或持久状态。
- AI Information Processor 的 `result_json` 可直接容纳 failure/trace 摘要，无需数据库迁移。
- 不建议把完整 transport trace 永久写入每条账本；账本保存终态和压缩后的模型尝试，详细 transport trace 进入日志/trace backend，避免 JSON 膨胀。
- 应用层临时字段 `fallback_candidates` 升级后迁移为事实字段 `attempted_models`/压缩 attempts；保留一段兼容读取期。

### 9. API 契约

- 新公开类型：`LLMCallError`、`CallTrace`、`ModelAttempt`、`TransportAttempt`，从包根导出。
- `ChatResult.trace` 为新增可选字段；旧字段保留至少一个兼容版本。
- `LLMCallError.trace` 对进入调用编排后的错误始终存在；本地 pre-request 拒绝不保证存在。
- `safe_error_message` 可为 `None`，调用方不得依赖自然语言字符串做业务判断。
- `error_kind` 是稳定机器契约；`http_status` 是正交字段。
- `chat()`/`chat_json()` async 与 sync 保持一致；`chat_stream()` 明确列为本期不覆盖。

### 10. 性能与扩展性

- trace 大小由 fallback 数量 × 每模型重试次数决定；默认配置下很小，但仍需事件上限和截断标记。
- 只保存标量和短字符串，不复制 messages/response，避免大对象常驻内存。
- monotonic 计时和 append 的开销可忽略；不在热路径做复杂正则，脱敏只处理已截断的短错误摘要。
- 10 倍负载下主要风险是高基数 metrics 和过量日志，不是 trace 对象本身。
- 100 倍负载下应采样详细成功 trace，但失败 trace不能采样丢失；采样由消费方 hook 决定。

### 11. 设计与用户体验

不涉及图形 UI。开发者体验方面：

- 默认异常字符串保持简短，首行包含 error_kind、最后尝试模型和 request_id。
- 完整细节通过结构化属性读取，不要求用户解析字符串。
- README 提供成功 fallback、终态失败和安全落账三个示例。
- 明确解释“requested model”“last attempted model”“final model”的区别。

## 实施顺序

1. PR 1：类型与契约——`CallTrace`、attempt 类型、`LLMCallError` 层级、公开导出、兼容测试。
2. PR 2：编排接入——route decision、model attempt、成功/失败 trace、修复错误统计。
3. PR 3：transport 接入——async/sync retry 逐次记录、错误摘要与脱敏。
4. PR 4：消费项目迁移——直接消费公共 trace，删除私有属性推断，更新账本和指标。
5. 独立 PR：重新评审 timeout/truncation 的重试行为；不与可观测性变更混合上线。

## 明确延期和不做

- 不在本期实现通用 availability fallback。
- 不把 generic HTTP 400 自动当作 schema 不兼容。
- 不在本期支持 `chat_stream()` 完整 fallback trace。
- 不引入 OpenTelemetry、Prometheus 或数据库依赖。
- 不修改生产 fallback 模型配置。
- 不在本期顺手改变 timeout/truncation 重试语义。

## CEO REVIEW SUMMARY

- **Mode:** SELECTIVE EXPANSION
- **Strongest challenges:** 当前错误丢失实际路由事实；错误分类与重试动作耦合；timeout 文档与真实行为冲突。
- **Recommended path:** 采用成功/失败统一的分层 `CallTrace`，兼容式扩展异常层级，先完成事实记录，再单独评审行为策略。
- **Accepted scope:** 公共错误契约、route/model/transport 三层尝试链、安全错误摘要、async/sync parity、测试、hook、消费项目迁移。
- **Deferred:** timeout/truncation 重试策略、流式 trace、availability fallback、完整 provider 策略引擎。
- **NOT in scope:** 更换生产模型、记录 prompt/完整上游响应、引入重型 telemetry 依赖、修改业务数据库 schema。

## ENGINEER REVIEW

日期：2026-07-12  
评审对象：结构化调用轨迹实施方案  
范围决定：完整目标不缩水，拆成三个可独立发布的阶段；本轮只允许第一阶段进入实施。

### Step 0：范围挑战

原方案跨越错误层级、fallback 编排、JSON self-correction、同步/异步驱动和 retry 层，预计修改超过 8 个文件并引入超过 2 个公共类型。一次完成的爆炸半径过大。已选择分阶段：

1. **阶段 1 / v0.6.0：模型级事实**——公共错误契约、路由决策、模型尝试、成功/失败 `CallTrace`、兼容测试。
2. **阶段 2：transport 级事实**——每次 HTTP 重试、安全错误摘要、`on_trace`、明确的逻辑调用/模型尝试统计。
3. **阶段 3：消费者迁移**——AI Information Processor 直接读取公共 trace，删除私有属性推断。

第一阶段不加入 `TransportAttempt`、`safe_error_message`、`on_trace`，不改变 timeout/retry 策略，也不改变现有 `LLMStats.total_calls` 口径。`FallbackExhaustedError` 不是第一阶段上线门禁；已有 `ContentPolicyError` 可先携带完整 trace。第一阶段只给已有 `LLMError` 体系附加 trace，不 blanket-wrap 任意未知异常，避免 minor 版本破坏依赖原异常类型的调用方。

### What already exists

| 子问题 | 已有实现 | 复用方式 |
|---|---|---|
| async/sync 共用编排 | `BaseClient` generator + 两个 driver | trace builder 放在共享编排，不复制两套状态机 |
| fallback 路由 | `_content_fallback_orchestrator` | 在现有模型循环记录 route/model 事实 |
| JSON 格式降级与 self-correction | `_json_attempt` 每次 `yield _ChatRequest` | 每个 yield 对应一个模型请求事实 |
| 底层异常链 | retry 使用 `raise ... from exc` | 从 cause 链提取 HTTP 状态，保留原异常 |
| 成功结果元数据 | `ChatResult` 已有 model/provider/request_id | additive 增加 `trace`，旧字段继续工作 |
| 失败类型 | `LLMError` 及具体子类 | 插入兼容父类 `LLMCallError`，不替换具体异常 |
| 测试基础 | pytest + pytest-httpx，async/sync/fallback/JSON 测试齐全 | 在现有文件追加契约和回归矩阵 |

### Architecture Review

#### 最终第一阶段数据流

```text
chat() / chat_json()
        │
        ├── pre_request 阻止 ───────────────► SkipRequestError（无 CallTrace）
        │
        ▼
  _content_fallback_orchestrator
        │ 创建可变 _CallTraceBuilder
        │
        ├── sensitive prescan
        │     ├── 未执行/不可用 ───────────► RouteDecision(not_evaluated)
        │     ├── 未命中 ─────────────────► RouteDecision(primary_selected)
        │     └── 命中 ───────────────────► RouteDecision(primary_skipped)
        │
        ├── 每个 _ChatRequest yield ───────► ModelAttempt started
        │     ├── HTTP/transport error ────► outcome=error
        │     ├── 200 refusal ─────────────► outcome=response_received,
        │     │                                response_classification=content_policy
        │     └── 普通响应 ────────────────► outcome=response_received
        │
        ├── JSON parse/self-correction
        │     ├── parse success ───────────► CallTrace.final_outcome=success
        │     └── exhausted ───────────────► CallTrace.final_outcome=json_parse
        │
        ├── success ── freeze ─────────────► ChatResult.trace
        └── failure ── freeze ─────────────► LLMCallError.trace
```

#### 已接受的架构修正

- `ModelAttempt` 只描述上游交互：`response_received` 不等于整个 `chat_json()` 成功。
- JSON 解析结果只写入 `CallTrace.final_outcome`。同一次调用不会再被描述成“模型 HTTP 失败”。
- 路由决策和实际请求分开。敏感词预检跳过主模型不进入 attempted models。
- 每次 `_json_attempt` yield 都是一条独立 `ModelAttempt`，因此 schema→object 与 self-correction 都可区分。
- 公开对象为 frozen dataclass + tuple；内部 builder 可变，终态冻结。
- 不使用 HTTPX client-wide event hooks记录第一阶段事实。它们缺少本次逻辑调用的 fallback 上下文，且同步/异步 hook 签名不同；共享 generator 是现成且更准确的接入点。

#### 生产失败场景

| 场景 | 处理 | 用户/调用方看到什么 |
|---|---|---|
| trace builder 自身抛异常 | builder 操作必须为无外部 IO 的简单 append；测试覆盖 | 不允许覆盖原始 LLM 结果；实现时使用最小无失败路径 |
| 未知异常越过 retry | 保持原异常类型，不伪装成稳定 LLM 运行错误 | 原异常和 traceback；阶段 1 不承诺 trace |
| pre_request hook 抛异常 | 不伪装成上游调用失败 | 保持原异常；无 trace |
| fallback 因视觉能力过滤为空 | route decision 记录无合格候选 | 现有业务行为不变，错误上下文明确 |
| 总 deadline 在 fallback 前耗尽 | 冻结已完成 attempts | 终态错误带不完整但真实的 trace |

### Code Quality Review

#### 模块边界

- 新建 `_trace.py`：公开 frozen 类型、内部 builder、显式 `to_dict()`。
- `errors.py`：异常层级和 `error_kind/http_status/trace`；异常类不用 dataclass。
- `_base.py`：只负责在既有状态机节点调用 builder，不放序列化和脱敏逻辑。
- `_types.py`：`ChatResult.trace: CallTrace | None`，避免把 trace 类型重复定义。
- `client.py` / `sync.py`：只在最终成功或失败边界冻结/附加，不复制路由规则。

#### 错误分类兼容

- 保留 `classify_error()` 当前返回异常 class 的行为，避免破坏模块级使用者。
- 第一阶段增加内部 `describe_error()` 或等价 helper，返回 `error_kind/http_status`；第二阶段复用它增加 retry action 和安全摘要。
- 所有已有具体错误继续可捕获；`SkipRequestError` 不继承 `LLMCallError`。
- 任意未知异常不统一包装。未来若要给畸形上游响应增加 `InvalidResponseError`，需单独定义捕获边界和兼容说明。
- content fallback 内部重新抛出 `ContentPolicyError` 时必须使用 `raise ... from previous_error`；终态异常的 cause 链必须能追到最后一个具体 HTTP/拒绝错误。
- `_is_format_error()` 的 generic `"unsupported"` 匹配必须收紧：只有错误上下文同时明确指向 `response_format`、`json_schema` 或等价 capability 字段时才降级。
- 修复 `_base.py` 的 `type(Exception).__name__`，但不在第一阶段重定义整个 `LLMStats` 语义。

#### 统计口径根治路径

当前 `_extract_result()` 在 JSON 解析前记录 success，一次 self-correction 可能增加多次成功计数。为避免第一阶段同时做结构和行为变更：

- 阶段 1：保持旧计数行为，只修复错误类型记录 bug；新 trace 是可信事实源。
- 阶段 2：新增明确的逻辑调用数、模型尝试数、transport 尝试数；标记含糊的 `total_calls` 为兼容字段并给出迁移说明。
- 阶段 3：消费项目切换新口径后，再决定旧字段的废弃窗口。

### Test Review

框架：pytest + pytest-httpx；基线全套 pytest、ruff、mypy 均通过。

```text
CODE PATHS                                             CONSUMER FLOWS
[+] _trace.py                                          [+] 普通 chat 成功
  ├── [GAP] builder → frozen CallTrace                   └── [GAP] ChatResult.trace 完整
  ├── [GAP] tuple/frozen 不可变                         [+] 敏感词预检
  └── [GAP] to_dict 不含未声明对象                       ├── [GAP] 未命中走主模型
                                                       ├── [GAP] 命中跳过主模型
[+] errors.py                                            └── [GAP] detector unavailable
  ├── [GAP] 旧异常捕获关系保持
  ├── [GAP] SkipRequestError 不属于 LLMCallError        [+] content fallback
  ├── [GAP] HTTP cause → status/kind                     ├── [GAP] 200 refusal → fallback 成功
  └── [GAP] unknown exception 保持原类型                 ├── [GAP] HTTP policy → fallback 成功
                                                         └── [GAP] 全部拒绝仍可 catch ContentPolicyError
[+] _base.py
  ├── [GAP] generic HTTP 400                            [+] chat_json
  ├── [GAP] schema unsupported → object                  ├── [GAP] schema→object 两条 attempt
  ├── [GAP] JSON parse fail no correction                ├── [GAP] self-correction 后成功
  ├── [GAP] self-correction success                      └── [GAP] self-correction 耗尽
  ├── [GAP] content fallback cause 链保真
  └── [GAP] 真实异常类型写入 stats                      [+] async/sync parity
                                                         ├── [GAP] async 同一事实
                                                         └── [GAP] sync 同一事实

PLANNED COVERAGE: 25/25 paths required
QUALITY TARGET: 所有路径 ★★★（行为 + 边界 + 错误）
E2E/EVAL: 无 prompt 或模型质量变化，不需要 LLM eval；公共库使用 mock integration tests。
```

#### 必须新增的测试

- `tests/test_trace.py`：冻结、序列化、route/model attempt 结构和截断标记。
- `tests/test_errors.py`：新继承关系、cause 链、未知异常不包装、HTTP 状态和错误类别。
- `tests/test_client.py` / `tests/test_sync.py`：普通成功和 async/sync parity。
- `tests/test_client_sensitive.py`：prescan hit/miss/unavailable 和无合格 fallback。
- `tests/test_client_fallback.py`：200 refusal、HTTP policy、all refused、generic 400。
- `tests/test_structured_output.py`：schema→object、self-correction success/exhausted、真正 JSON parse failure。
- `tests/test_types.py`：`ChatResult.trace=None` 的向后兼容构造。
- `tests/test_api_compatibility.py`（或现有最接近文件）：包根导出和旧异常 catch 契约。

所有上述测试是阶段 1 的提交门禁，不延期。实现后必须执行：

```bash
uv run pytest
uv run ruff check src tests
uv run mypy src
```

### Performance Review

- 第一阶段只保存短标量，不复制 prompt、messages、payload、response 或 headers。
- `ModelAttempt` 数量由 fallback 链和 JSON self-correction 次数决定。默认很小，但配置链可任意长；保留 `max_trace_events=100`、`truncated`、`dropped_events`，禁止静默丢失。
- 使用 `time.monotonic()` 计算耗时；仅顶层 UTC 时间用于序列化。
- frozen dataclass 的微小构造开销相对网络延迟可忽略。
- metrics 高基数问题推迟到阶段 2 的 `on_trace` 设计处理；第一阶段不自动发出外部指标。

### Failure Modes

| 新路径 | 生产失败方式 | 测试 | 错误处理 | 是否静默 |
|---|---|---|---|---|
| trace 构建 | 分支漏记/重复记 attempt | 精确序列断言 | builder 集中管理 | 否 |
| 异常附加 trace | 包装后旧 catch 失效 | 继承契约回归测试 | 兼容继承 | 否 |
| JSON self-correction | 多次响应被误记成一次 | attempt 数量/顺序断言 | final outcome 分层 | 否 |
| prescan skip | 主模型被误记为 attempted | route 与 attempts 分离测试 | 明确 decision | 否 |
| trace 上限 | 长链丢失尾部事实 | truncation 测试 | flag + dropped count | 否 |
| unknown exception | 被误包装导致旧 catch 失效 | 原异常类型断言 | 不 blanket-wrap | 否 |

关键静默缺口：0。所有失败路径都有计划测试和结构化终态。

### Distribution

本次没有引入新的制品类型。项目继续作为 GitHub Git dependency 分发。阶段 1 合并后：

1. `pyproject.toml` bump 到 `0.6.0`；
2. README / integration guide 记录新公共契约和兼容性；
3. push/tag 后由消费者更新 git commit pin；
4. AI Information Processor 只在阶段 3 更新依赖，阶段 1/2 不要求立即迁移。

仓库当前没有包发布 workflow；本期不新增 PyPI/GitHub Release pipeline，因为消费者使用 Git URL/commit pin，这不是新制品分发缺口。

### Parallelization

Sequential implementation, no parallelization opportunity。阶段 1 的 trace 类型、异常层级、共享编排和测试互相依赖并集中修改 `src/llm_compat/`；拆 worktree 会制造冲突。阶段 2 依赖阶段 1 公共契约，阶段 3 依赖已发布版本。

### NOT in scope

- transport/HTTP 逐次 trace：阶段 2；避免第一阶段触碰 retry 行为。
- `safe_error_message` 和脱敏：阶段 2；第一阶段只存受控 kind/status。
- `on_trace` 与外部 telemetry：阶段 2；避免新增 hook 语义。
- `LLMStats` 新口径：阶段 2 根治；第一阶段不打断历史趋势。
- AI Information Processor 私有推断清理：阶段 3，等待库公开契约发布。
- timeout/truncation 是否重试：独立行为 PR。
- 逻辑调用总 deadline：当前同模型 schema 降级/self-correction 会复用旧预算；独立行为 PR 逐次重算，第一阶段只记录真实终态。
- fallback 重复模型/循环配置校验：现有校验未实现设计文档承诺；单独修复，不阻塞当前误分类根治。
- 通用 availability fallback、流式 fallback trace、生产模型更换：不属于本问题。

### Implementation Tasks

- [ ] **T1 (P1, human: ~4h / Codex: ~30min)** — Trace contract — 实现 frozen `CallTrace`、`RouteDecision`、`ModelAttempt` 与内部 builder
  - Surfaced by: Architecture — 路由决策、模型交互和终态必须分层
  - Files: `src/llm_compat/_trace.py`, `src/llm_compat/_types.py`, `tests/test_trace.py`, `tests/test_types.py`
  - Verify: `uv run pytest tests/test_trace.py tests/test_types.py`
- [ ] **T2 (P1, human: ~3h / Codex: ~25min)** — Error contract — 增加兼容式 `LLMCallError` 和结构化错误上下文
  - Surfaced by: Code Quality — 失败必须稳定、可机器读取且保留旧 catch
  - Files: `src/llm_compat/errors.py`, `src/llm_compat/__init__.py`, `tests/test_errors.py`
  - Verify: `uv run pytest tests/test_errors.py`
- [ ] **T3 (P1, human: ~6h / Codex: ~45min)** — Orchestration — 在共享 generator 记录 route/model attempts、保全 cause 并附着终态 trace
  - Surfaced by: Architecture — async/sync 必须共享同一事实源
  - Files: `src/llm_compat/_base.py`, `src/llm_compat/client.py`, `src/llm_compat/sync.py`
  - Verify: fallback、structured output、sensitive、sync 测试集；最终异常可追到具体 cause
- [ ] **T4 (P1, human: ~5h / Codex: ~40min)** — Regression matrix — 覆盖 25 条成功、fallback、格式降级、解析和兼容路径
  - Surfaced by: Test Review — 新公共契约当前覆盖为 0，必须随实现完成
  - Files: `tests/test_client.py`, `tests/test_sync.py`, `tests/test_client_sensitive.py`, `tests/test_client_fallback.py`, `tests/test_structured_output.py`
  - Verify: `uv run pytest`
- [ ] **T5 (P2, human: ~2h / Codex: ~15min)** — Release/docs — 发布 v0.6.0 并写清字段语义与阶段边界
  - Surfaced by: Distribution/DX — Git dependency 消费者需要明确升级契约
  - Files: `pyproject.toml`, `README.md`, `docs/guides/integration-guide.md`, `docs/design/decision-log.md`
  - Verify: `uv run ruff check src tests && uv run mypy src`
- [ ] **T6 (P3, human: ~1d / Codex: ~1h)** — Metrics migration — 阶段 2 增加逻辑调用、模型尝试和 transport 尝试的明确计数
  - Surfaced by: Code Quality — `total_calls` 当前混合多种粒度
  - Files: `src/llm_compat/_types.py`, `src/llm_compat/retry.py`, relevant tests/docs
  - Verify: 新旧计数兼容测试 + 全量测试
- [ ] **T7 (P3, human: ~1d / Codex: ~1h)** — Retry/config debt — 修正总 deadline 预算与重复/循环 fallback 配置校验
  - Surfaced by: Outside voice — 同模型多次 yield 复用旧预算；现有配置校验未覆盖重复/循环
  - Files: `src/llm_compat/_base.py`, `src/llm_compat/_compat.py`, relevant tests/docs
  - Verify: fake clock deadline 测试 + duplicate/cycle 配置矩阵

### Outside Voice

隔离技术复核发现 5 项，均已吸收且没有跨模型方向冲突：

1. 不 blanket-wrap 未知异常，保持 minor 版本兼容。
2. content fallback 的 `raise ... from` cause 保真列为阶段 1 门禁。
3. 总 deadline 语义从阶段 1 移出，单独修行为。
4. generic HTTP 400 的 schema 降级判定明确收紧并加入阶段 1 测试。
5. 重复/循环 fallback 校验记录为后续任务，不伪称当前已经实现。

No cross-model tension — both reviewers agree.

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | plan challenge | Scope & strategy | 1 | CLEAR | 采用结构化调用轨迹，明确延期行为策略 |
| Codex Review | outside voice | Independent 2nd opinion | 1 | ABSORBED | 5 个实施缺口全部纳入，无方向冲突 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 29 个路径/问题，0 critical gaps，0 unresolved |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | N/A | 后端公共库，无 UI 改动 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | N/A | README 与兼容契约已进入实施任务 |

- **CROSS-MODEL:** 两边同意分阶段发布、保持异常兼容、区分 response_received 与最终 JSON 结果；outside voice 的 5 个缺口已吸收。
- **VERDICT:** CEO + ENG CLEARED — 阶段 1 可以进入实施。

NO UNRESOLVED DECISIONS
