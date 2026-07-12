---
status: implemented-pending-final-gate
branch: main
created_at: 2026-07-12T07:31:54-04:00
topic: llm-compat fallback 可观测性阶段 1
review_status: CEO + ENG CLEARED; CODEX GATE PENDING
---

# LLM Compat fallback 可观测性实施交接

## 0. 实施进度（2026-07-12）

- T1 Trace contract：已完成，新增不可变 `CallTrace`、`RouteDecision`、`ModelAttempt` 和
  内部 builder。
- T2 Error contract：已完成，新增兼容父类 `LLMCallError`，稳定元数据和包根导出。
- T3 Shared orchestration：已完成，async/sync 共享模型级轨迹，收紧 generic HTTP 400
  降级并修复错误统计类型。
- T4 Regression matrix：已完成，正式 pytest 门禁当前为 366 项。
- T5 Release/docs：代码与用户文档已更新到 v0.6.0。
- 正式门禁：`uv run pytest`、`uv run ruff check src tests`、`uv run mypy src` 均通过。
- Codex gate 第 1 轮：发现 2 个 P1、1 个 P2，均已修复并补回归测试；连续清洁轮次
  尚未开始累计。
- Codex gate 第 2 轮：新增 1 个 P2（prescan 后续候选 trigger），已修复并补回归测试；
  连续清洁计数仍为 0/2。
- Codex gate 第 3 轮：无新增实质性意见，曾累计 1/2。
- Codex gate 第 4 轮：新增 1 个 P2（`does not support` capability 表达），已修复并补
  回归测试；连续清洁计数重置为 0/2。
- Codex gate 第 5 轮：新增 1 个 P1（unsupported schema keyword 被误认为 capability），
  已改为受限句式匹配并补回归测试；连续清洁计数重置为 0/2。
- Codex gate 第 6 轮：新增 1 个 P1（带引号的 `response_format of type json_schema`
  capability 文案），已增加受限模式并补回归测试；连续清洁计数重置为 0/2。
- Codex gate 第 7 轮：新增 1 个 P1（schema keyword 夹在 support 与 format 之间），已将
  subject-denial 模式收紧为直接格式目标并补回归测试；连续清洁计数重置为 0/2。
- Codex gate 第 8 轮：无新增实质性意见，曾累计 1/2。
- Codex gate 第 9 轮：新增 1 个 P2（quoted `json_schema`），已仅规范化两个已知格式 token
  的外层引号并补回归测试；连续清洁计数重置为 0/2。
- Codex gate 第 10 轮：新增 2 个 P1（JSON-escaped quote 与 format 后 keyword suffix），
  已支持转义引号并对 schema-feature suffix 增加负向断言；连续清洁计数重置为 0/2。
- Codex gate 第 11 轮：新增 1 个 P1（其他 capability pattern 的 schema-feature 后缀），
  已在所有 pattern 前统一拒绝 `json_schema` 与 schema-feature 近邻错误；连续清洁计数重置
  为 0/2。
- Codex gate 第 12 轮：新增 1 个 P2（结构化 metadata 干扰 message 分类），已优先提取
  `error.message`/字符串 error，非 JSON 才回退正文；连续清洁计数重置为 0/2。
- Codex gate 第 13 轮：新增 1 个 P2（结构化 JSON 缺少 message 时仍扫描 metadata），
  已对无认可字符串 message 的结构化响应直接判定非 capability；连续清洁计数重置为 0/2。
- 最终完成条件：仍需独立 Codex gate Review 连续两轮无新增实质性意见。

## 1. 当前结论

fallback HTTP 失败被消费项目误记为 `json_parse_failed` 的问题已经确认存在。
CEO Review、Engineer Review 和独立技术复核均已完成，阶段 1 可以直接进入实施，
没有未决策项。

完整方案与评审：

- `memory/llm-compat-fallback-observability-ceo-review.md`
- `/home/zlx/.gstack/projects/zj1123581321-llm-compat/zlx-main-eng-review-test-plan-20260712-072125.md`

## 2. 已拍板的核心设计

- 成功和失败共用 `CallTrace`：成功挂在 `ChatResult.trace`，失败挂在已有
  `LLMError` 子类。
- 阶段 1 只记录 `RouteDecision` 和 `ModelAttempt`；HTTP/transport 逐次重试留到
  阶段 2。
- 敏感词预检跳过主模型属于 route decision，不属于实际 attempt。
- `ModelAttempt.outcome=response_received` 只表示拿到上游响应；JSON 解析结果写在
  `CallTrace.final_outcome`。
- 保留已有具体异常捕获兼容；新增 `LLMCallError` 父类，但 `SkipRequestError` 不继承它。
- 不 blanket-wrap 任意未知异常，避免 minor 版本破坏原异常捕获。
- content fallback 重抛必须使用 `raise ... from previous_error`，保留最终具体 cause。
- generic HTTP 400 不自动触发 schema→object；只有错误明确指向
  `response_format`、`json_schema` 或等价 capability unsupported 时才降级。
- 阶段 1 不改变 timeout、retry 或 `LLMStats.total_calls` 行为，只修复错误类型被记录成
  `"type"` 的 bug。

## 3. 阶段 1 实施任务

### T1：Trace contract

- 新增 `src/llm_compat/_trace.py`。
- 实现 frozen `CallTrace`、`RouteDecision`、`ModelAttempt`。
- 内部使用可变 builder，返回或抛错前冻结。
- `ChatResult` 新增向后兼容的可选 `trace` 字段。
- trace 不得包含 prompt、messages、payload、响应正文、headers、API key 或原始异常。

### T2：Error contract

- 新增兼容式 `LLMCallError` 父类。
- `FatalError`、`RetryableError`、`ContentPolicyError`、`JSONParseError` 等已有运行错误
  保持原捕获兼容。
- `SkipRequestError` 不继承 `LLMCallError`。
- 为运行错误提供稳定的 `error_kind`、`http_status` 和 `trace`。
- 不 blanket-wrap 未知异常；底层异常继续通过 `__cause__` 保留。
- 更新包根公共导出。

### T3：Shared orchestration

- 在 `BaseClient` 共享 generator 中记录 route decision 和 model attempt，确保 async/sync
  语义一致。
- 每次 `_ChatRequest` yield 对应一条 `ModelAttempt`，schema→object 和 self-correction
  必须能区分。
- JSON 解析成功或失败只写入 `CallTrace.final_outcome`，不得污染上游可用性指标。
- content fallback 重抛时保全 cause 链。
- 收紧 generic HTTP 400 的格式降级判断。
- 修复 `_base.py` 的 `type(Exception).__name__` 错误统计 bug。

### T4：Regression matrix

必须覆盖：

- 普通 chat 成功；
- async/sync trace 一致；
- prescan hit/miss/unavailable；
- 主模型被跳过时不进入 attempts；
- 200 refusal fallback；
- HTTP content-policy fallback；
- fallback 全拒绝且旧 `ContentPolicyError` catch 仍有效；
- generic HTTP 400；
- 明确 schema unsupported 后降级成功；
- self-correction 成功和耗尽；
- 真正 JSON 解析失败；
- cause 链保真；
- 未知异常不包装；
- trace 不可变、序列化安全和截断标记；
- `ChatResult` 不传 trace 时仍可按旧方式构造；
- 包根公共导出和异常继承契约。

### T5：Release/docs

- 版本更新为 `0.6.0`。
- 更新 `README.md`、integration guide、decision log。
- 明确 requested model、skipped model、attempted model、final model 和 final outcome 的
  区别。
- 写明阶段 2/3 延期边界。

## 4. 明确不做

- `TransportAttempt` 或每次 HTTP 重试明细；
- `safe_error_message` 与脱敏实现；
- `on_trace` hook；
- `LLMStats` 新统计口径；
- timeout/truncation 重试行为修改；
- 总 deadline 行为修复；
- 重复/循环 fallback 配置校验；
- availability fallback；
- `chat_stream` 完整 trace；
- AI Information Processor 消费项目迁移；
- 修改生产 fallback 模型。

## 5. 工作树与安全注意事项

- 当前 `uv.lock` 已有与本任务无关的修改，必须保留，不得覆盖或回滚。
- `memory/` 是评审材料，当前未跟踪，不得删除。
- 不使用 `git reset --hard`、`git checkout --` 等破坏性命令。
- 不修改 `/home/zlx/projects/personal/AI_Information_processor-llm-observability`。
- 除非用户明确要求，不 commit、push 或部署。
- 普通工程选择按最佳实践直接决定；只有影响很大、确实拿不准且会改变已批准方向时才询问用户。

## 6. 验证门禁

完成后必须执行：

```bash
uv run pytest
uv run ruff check src tests
uv run mypy src
```

当前基线三项均通过。若实现后失败，继续诊断并修复，直到通过或出现真实外部阻塞。

## 7. 新 session 执行 Prompt

```text
请在 /home/zlx/projects/personal/llm-compat 中实施 fallback 可观测性“阶段 1 / v0.6.0”。

开始前完整阅读：

1. CLAUDE.md
2. docs/sessions/260712-0731-llm-compat-observability/HANDOFF.md
3. memory/llm-compat-fallback-observability-ceo-review.md
4. /home/zlx/.gstack/projects/zj1123581321-llm-compat/zlx-main-eng-review-test-plan-20260712-072125.md

CEO Review 和 Engineer Review 已完成，结论为 CEO + ENG CLEARED。不要重新讨论已拍板的
产品方向；直接实施 HANDOFF.md 第 3 节的 T1-T5，并严格遵守第 4 节的范围边界和第 5 节的
工作树注意事项。

目标是根治 fallback HTTP 失败被误记为 json_parse_failed：成功和失败获得统一、兼容、
可审计的模型级 CallTrace，同时不改变 retry、timeout、旧 LLMStats 口径或生产 fallback 行为。

使用 apply_patch 修改文件。普通工程选择按最佳实践直接决定；只有影响很大、确实拿不准且会
改变已批准方向的问题才询问我。不要 commit、push 或部署。

实现后运行：

uv run pytest
uv run ruff check src tests
uv run mypy src

若测试失败，继续诊断和修复。最终汇报公共契约、修改文件、关键测试、三项验证结果、延期内容、
兼容风险和未完成项。
```
