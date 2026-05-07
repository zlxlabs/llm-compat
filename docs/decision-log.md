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
