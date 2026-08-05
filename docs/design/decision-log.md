# 决策日志

记录设计过程中的关键决策及其推理过程。

## 2026-05-07: 是否支持 DeepSeek `thinking` 参数

**问题**: DeepSeek V4 新增了 `thinking: {type: "enabled"/"disabled"}` 对象，当前系统只支持 `reasoning_effort`。

**讨论过程**:
1. 初始方案：只走 OpenAI 协议，不做 provider 特化 → `reasoning_effort` 透传
2. 反驳：用户指出 VideoTranscriptAPI 项目已经通过模型名检测实现了 provider 翻译，即使走 New API 代理也能工作
3. 关键洞察：`detect_provider()` 是按模型名（fnmatch）识别 provider，不是按 base_url。模型名在配置里明确指定，与是否走代理无关

**决定**: 支持。通过模型名 fnmatch 检测 provider，按族翻译 thinking 参数。

---

## 2026-05-07: 是否抽成独立包

**问题**: 10+ 项目都有 LLM 调用需求，是否值得抽包？

**讨论过程**:
1. 初始评估：可复用代码约 150 行，维护包的开销可能大于复制
2. 用户补充：10+ 项目，经常切换厂商模型，JSON 兼容和 thinking effort 都是问题
3. 重新评估：项目数 > 5 且频繁切换厂商 → 过了抽包阈值

**决定**: 抽包。DeepSeek 停服迁移（2026-07-24）是个硬 deadline，统一包可以一次修改覆盖所有项目。

---

## 2026-05-07: 是否使用现有方案（LiteLLM / aisuite）

**问题**: 市面上是否有现成工具可以复用？

**调研结果**:
- LiteLLM (45K stars): 功能与 New API 重叠，太重 (14.8MB)
- aisuite: 设计为直连各家 API，与 New API 代理架构不匹配
- 其他小项目: 都是代理/网关方向，不是客户端翻译层

**决定**: 自建。市场空白：没有轻量的 "客户端 payload 翻译" 库。

---

## 2026-05-07: reasoning_effort 值域设计

**问题**: `Literal` 硬编码还是 `str` 自由透传？

**讨论过程**:
1. Memos 当前用 `Literal["none", "low", "medium", "high"]` → 无法传 `"max"`
2. 各家值域不同且在演进，硬编码会不断需要更新
3. 但完全自由透传又失去了 "disabled" 的翻译语义

**决定**: 混合方案。
- `"disabled"` 作为特殊值，触发 provider 翻译逻辑
- `"none"` 作为 legacy 别名，归一到 `"disabled"` + deprecation warn
- 其他值（`"low"` / `"high"` / `"max"` / 任意字符串）透传或按 provider 映射
- `None` 表示不设置，使用 provider 默认

---

## 2026-05-07: 同步 vs 异步

**问题**: 包提供同步 API 还是异步 API？

**考量**:
- 主要消费者 Memos Auto 是 FastAPI 异步项目
- VideoTranscriptAPI 是同步项目
- 约 50/50 分布

**决定**: 异步优先（AsyncClient），同步可通过 `asyncio.run()` 调用或后续加 sync wrapper。大多数新项目倾向异步。

---

## 2026-05-07: 日志框架选择

**问题**: 用 loguru 还是标准 logging？

**考量**:
- Memos Auto 用 loguru
- 标准 logging 更通用，loguru/structlog 都可以 bridge 接入
- 包不应该强制消费者使用特定日志框架

**决定**: 标准 `logging` 模块。消费者自行配置 handler。

---

## 2026-05-13: URL 关键词格式：JSON vs 纯文本

**问题**: `_keyword_cache.py` 要求 URL 返回 `{"words": [...]}` JSON 格式，但敏感词场景更适合纯文本（每行一词）。

**讨论过程**:
1. 消费者项目的敏感词 URL 返回纯文本格式，与 JSON 要求不匹配
2. JSON 的 `{"words": [...]}` 格式对于简单词表来说过于复杂
3. 考虑过自动探测（JSON/纯文本），但增加复杂度且两种格式长期共存不利于维护

**决定**: 完全切换为纯文本格式（每行一词，`#` 注释，空行忽略）。Breaking change，但格式更简单通用。Collector 新增 `/words.txt` 端点返回纯文本，保留 `/words` JSON 端点供 `_collector.py` 的 hash 增量更新使用。

---

## 2026-05-13: sensitive_words_url 的 detector 重建策略

**问题**: `SensitiveDetector` 使用 Aho-Corasick 自动机，构造后不支持动态加词。轮询刷新词库后需要重建 detector。

**考虑的方案**:
1. **回调机制**: `_keyword_cache` 支持 `on_refresh` 回调，刷新时主动触发重建 → 增加 `_keyword_cache` 复杂度和耦合
2. **版本计数器**: `_keyword_cache` 加计数器，`LLMClient` 每次请求做 O(1) 整数比较，变化时才重建 → 最小改动，低耦合

**决定**: 版本计数器方案。`_cache_version` 字典 + `get_cache_version()` API，`LLMClient._get_sensitive_detector()` 做懒重建。相比 LLM API 调用延迟，O(1) 整数比较可忽略。

---

## 2026-07-12: fallback 可观测性采用统一模型级 CallTrace

**问题**: fallback 的 HTTP 失败只保留异常，没有保存敏感词预检、实际模型和格式降级事实。
消费项目将失败转换为 `None` 后，会把上游 HTTP 失败误记为 `json_parse_failed`。

**决定**:

1. 成功通过 `ChatResult.trace`、稳定运行失败通过 `LLMCallError.trace` 暴露同一种不可变
   `CallTrace`。
2. 路由决策与真实模型请求分层：预检跳过只写 `RouteDecision`；每个共享 generator yield
   写一条 `ModelAttempt`。
3. `response_received` 只表达上游可用性；JSON 解析结果只写 `final_outcome`。
4. 保留既有具体异常和字段，插入兼容父类 `LLMCallError`；`SkipRequestError` 不加入该层级，
   未知异常不统一包装。
5. generic HTTP 400 默认终止。只有错误同时明确指向 `response_format` / `json_schema` 等
   格式能力并表达不支持时，才执行 schema→object。
6. v0.6.0 限于模型级事实，不改变 retry、timeout、`LLMStats.total_calls` 或生产 fallback
   策略。

**延期**: transport retry 明细、安全错误摘要、`on_trace` 和新统计口径进入阶段 2；消费项目
迁移进入阶段 3；`chat_stream()` 完整 trace、availability fallback 和总 deadline 修复另行设计。

---

## 2026-08-04: `extra_body` 展开与 thinking 入口契约

**问题**: OpenAI SDK 的 `extra_body` 容器形状不适用于本库通过 httpx 直接发送请求的场景；
issue #7 暴露了 DeepSeek 思考关闭的静默失效，第二轮 review 实测还发现 provider-specific
`thinking` 字段会在 content fallback 中污染下一个 provider，形成 P1 风险。

**决定**:

1. 库内部只由 `reasoning_effort` 生成顶层 `thinking`；实际 wire 形状以各 provider 官方文档为准，
   不使用 OpenAI SDK 的 `extra_body` 容器形状。
2. 调用方经 `extra_body` 传入的 `thinking` 会被保留键阻断；思考开关的唯一入口是
   `reasoning_effort`。
3. content fallback 对每个目标模型重新翻译参数，不复用上一个 provider 的 provider-specific
   字段。

---

## 2026-08-05: 请求参数注入路径统一收口

**问题**: `thinking` 既可以从顶层 `**extra` 注入，也可以从 `extra_body` 展开；后者此前还
能覆盖翻译层生成的 `reasoning_effort`。两条注入路径分别修补会持续复发同类静默失效。

**决定**:

1. `_build_payload` 只接受具名的 `stream` 控制，并由库显式写入 base；调用方经 `**extra`
   提供的 `stream` 在进入构造器前丢弃。
2. 直接 `**extra` 与 `extra_body` 展开共用同一套保留键过滤逻辑；`thinking` 与
   `reasoning_effort` 只能由翻译层决定，调用方冲突记录 warning 并使用正确的具名入口。
3. `extra_body` 仍可提供非保留 provider 扩展字段，但不能覆盖 `model`、`messages`、`stream`、
   `extra_body`、`thinking`、`reasoning_effort`。

---

## 2026-08-05: reasoning_effort clamp 采用向上就近策略

**问题**: 不同 provider family 支持的 `reasoning_effort` 集合可能存在空洞。旧逻辑只要请求值
不低于 family 的最小值，就直接取最大值，导致请求落在空洞时发生跳档放大；DeepSeek 未文档化的
`medium` 也会因此被错误抬到 `xhigh`。

**决定**:

1. 对不在 `efforts` 集合内的请求值，按全局 `_EFFORT_RANK` 在该集合中向上取最近邻；向上无解时
   钳制到集合最大值，向下边界则取集合最小值。
2. `reasoning_effort` 表达质量下限而非成本上限，因此固定采用向上就近，避免把请求翻译成低于
   调用方意图的推理档位。
3. DeepSeek 的能力集合移除官方未文档化的 `medium`，所以请求 `medium` 明确翻译为 `high`。

该决策对应 [issue #8](https://github.com/zlxlabs/llm-compat/issues/8) 与
[issue #9](https://github.com/zlxlabs/llm-compat/issues/9)。
