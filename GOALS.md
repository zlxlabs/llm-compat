# 项目里程碑路线图

> 本文件管的是**跨语言消费能力**这条线（issue #13）。llm-compat 的 Python 库主体
> 已是成熟态（0.8.0 / 1023 测试 / mypy strict），不在本路线图范围内。

## 项目目标

- **目标**：让非 Python 项目（Bun/TS、Go 等）用上 llm-compat 的能力，而不是各自维护
  一份劣质的 `providers.py` 副本。
- **完成定义**：至少一个真实的非 Python 消费者在生产环境通过官方形态使用 llm-compat，
  且该消费者仓内不再存在手写的 provider 能力硬表。
- **当前激活里程碑**：M3-阶段0（痛点测量）。**M3 本体已阻塞，见下方状态说明。**

## 供给形态：分层，单一真相源

2026-08-06 架构审计确定的最终形态。三层不是三选一，是同一份逻辑的三种投喂方式：

```
        src/llm_compat/providers.py   ← 唯一真相源
                    │
      ┌─────────────┼─────────────────┐
      ↓             ↓                 ↓
  Python 库      HTTP shim        caps.json + conformance.json
      │             │                 │
  Python 项目    JS/Go/任何语言    兜底 + 一致性验证基座
  （零开销）     （改一行配置）    （不想多一跳 / 只需翻译表）
```

选型依据（详见「路线图审计」2026-08-06 一节）：

- 业界解决「一份逻辑给 N 种语言」共五种范式：sidecar 代理 / 每语言独立 SDK /
  原生核心+FFI 绑定 / 数据契约+各自实现 / 代码生成。
- 本项目单人维护 → 排除「每语言独立 SDK」（维护成本 ×N，主语言领先其他腐烂）。
- 核心价值是**控制流**（JSON 自修正、内容降级、重试）而非数据 → 数据契约范式有硬天花板，
  只能搬翻译表，搬不了逻辑。
- 核心已是 Python → FFI 绑定要求 Rust/C 重写，等于推倒重来。
- LLM 调用本身 300ms–5s，容器内网一跳 1–2ms（开销 <0.1%）→ 多一跳的代价可忽略。
  同领域先例一致：LiteLLM Proxy、Ollama、vLLM、Cloudflare AI Gateway、Portkey
  服务多语言时全部选 OpenAI 兼容端点形态。

## 里程碑路线图

### M1：`detect_provider` 结构化重构（原增量 1）

- **状态**：已完成
- **预期产出**：`detect_provider` 返回 `{family, matched}`，`matched` 信号可被行为层消费。
- **当前范围**：纯结构重构，零行为变更。
- **关键决策**：结构重构与行为变更分离到不同 PR（Beck），因本仓 P1 恰好都是静默型，
  混在一起审查时分不清哪行变化来自哪个意图。
- **已知阻塞**：无
- **推进前必须拿到的证据**：
  - [x] 现有 366 测试一字不改全绿；环境：本地 + CI；命令：`uv run pytest tests/ -q`
  - [x] gate primary SUCCESS + 变异复验通过；环境：CI；入口：PR #14
- **完成条件**：已合并 main（`4feb492`）

### M2：跨语言数据契约（原增量 2）

- **状态**：已完成（库内自洽层面）／入口层证据顺延至 M3
- **预期产出**：`caps.json`（10 family / 18 pattern）+ `conformance.json`（360 向量，
  逐条人工审定，`reviewed` 绑定指纹）+ `strict_unknown_models` 开关 + 消费指南。
- **当前范围**：只交付 L1 知识层与 L1+L2 行为契约，不交付任何语言的实现。
- **关键决策**：
  - 两份 JSON 不打进 wheel —— Python 侧从不回读，避免双真相源。
  - 两道防漂移闸靠 pytest 自动生效，不新增 workflow（原 T7 计划已被实测证伪：
    本仓 `gate.yml` 只调用复用 workflow，没有本地步骤可插）。
  - **本层的长期定位已在 2026-08-06 审计中改变**：从「跨语言主路径」降为
    「兜底路径 + shim/库的一致性验证基座」。360 条向量的价值不减反增 —— 它是
    M3 的验收 oracle。
- **已知阻塞**：无
- **推进前必须拿到的证据**：
  - [x] 1023 passed / mypy strict 干净 / 双导出物零漂移；环境：本地 + CI
  - [x] 360 向量 360 唯一 id / `reviewed: true`；环境：本地
  - [ ] **入口层证据顺延到 M3**：原定「AdCommentsPlatform 改读 caps.json」已作废
        （见审计结论），改由「AdComments 经 shim 跑通真实调用」一并兑现
- **完成条件**：已合并 main（`ceb4e08`，PR #16）。入口层证据条目挂到 M3。

### M3-阶段0：就地修消费者 + 测量（**当前激活**）

- **状态**：未开始（当前激活）
- **预期产出**：① AdComments 的 JSON 提取失败率数字；② 该失败路径本身被修好。
  两件事一次做完——埋点本来就要加，加完既拿到数字又直接改善了现状。
- **为什么是「修」而不只是「测」**（2026-08-06 Codex 代码级审查 finding #4）：
  D1 一直在比较 shim / 中心网关 / 扩大数据契约三个大方案，**却从未把「就地把唯一那个
  消费者修好」当选项评估过**，而那可能只要半天。它还彻底绕开了 shim 的两条架构级阻塞
  （见 M3 节），且不新增 SPOF。
- **具体做什么**（AdComments 仓，他仓开卡）：
  1. 给 `extractJson` 失败路径与 `empty` 分支补日志/计数
  2. TS 侧补返回结构校验（已用 `json_schema` + `strict:true`，但没验证返回是否真符合）
  3. 视情况加局部 fallback（JSON 解析失败时重问一次）
  4. 顺带离线重放拿基线数字（Supabase 已存真实评论原文，先例见
     `scripts/eval-thinking-regression.ts`）
- **为什么它在 M3 本体之前**：2026-08-06 两轮独立评审（5/10、6/10）证伪或削弱了
  M3 的两条核心价值论据——① 「AdComments 没有通用重试」是错的（`openai` Node SDK
  默认 `maxRetries=2`，连接失败/超时/429/5xx 已自动重试）；② 「手搓 JSON 提取是真痛点」
  强度未知（`analysis.ts:40` 用的是 `json_schema` + `strict:true`，正则抽取只是防御层）。
  M3 的设计选型没被推翻，但「现在就投 5.5 天去建」这个时机判断没有证据支撑。
- **怎么做**：**离线重放**——Supabase 已存有大量真实评论原文（`process.ts` 的
  `upsertData.content`），拉几百条 + 现有 prompt/模型跑一遍，直接统计 `extractJson`
  失败率。AdComments 仓已有同模式先例：`scripts/eval-thinking-regression.ts`。
  几小时出数，不改生产代码，不等流量。
- **为什么不能直接查日志**：`classifyChatError` 只产出 `gateway`/`timeout` 两种，
  `parse` 类型声明了但从未被赋值；`extractJson` 失败直接 `return null` 且零日志调用。
  **本计划举为核心痛点的那条失败路径，恰好是唯一完全没有留痕的。**
- **已知阻塞**：卡在他仓（AdCommentsPlatform，`~/projects/work/`），按跨仓边界需另开卡
- **判据（预先定死，不留事后解释空间）**：
  - 样本量：≥300 条真实历史评论
  - 失败口径：`extractJson` 返回 `null` 的比例
  - 触发值：**修完后残余失败率 > 2%** 且原因确属「模型返回结构不稳定」→ 重新评估 M3；
    否则 M3 降 backlog
- **推进前必须拿到的证据**：
  - [ ] 修复前后的失败率对比数字；环境：AdComments 本地离线重放；
        入口：一次性脚本 + Supabase 历史评论样本（≥300 条）
  - [ ] 修复已合并到 AdComments 主干，`extractJson` 失败路径有日志留痕
- **完成条件**：数字拿到 + 修复合并 + 按上述触发值做出 M3 去留裁决

### M3：HTTP shim —— OpenAI 兼容端点（新增，从 Deferred TODOS 提到主线）

- **状态**：**BLOCKED** —— 阻塞待 M3-阶段0 的结果，且**已知三条架构级阻塞未解**。
  完整决策记录：`~/.gstack/projects/zj1123581321-llm-compat/ceo-plans/2026-08-06-http-shim.md`
- **启动前必须先解决的架构级阻塞**（2026-08-06 Codex 代码级审查，前两轮文档审查全漏）：
  1. **`ChatResult` 无法重建 OpenAI 响应**（`_types.py:16-30`）——缺 `id`/`object`/
     `created`/`finish_reason`/`choices`/`tool_calls`/`logprobs`。**更严重的是
     `TokenUsage` 没有 `completion_tokens_details.reasoning_tokens`**，而 AdComments
     的 `logChatUsage` 正好读它，issue #13 那张 effort 矩阵就是靠它测的——
     接 shim 会让消费者丢掉验证「思考关没关成」的唯一指标
  2. **错误响应不兼容**——`retry.py` 把上游 HTTP 错误包成自定义异常，
     状态码/响应体/响应头无法原样返回，消费者 SDK 的错误分类会坏
  3. **`chat_json` 传字典 schema 时降级到 `json_object` 后不验证 schema**，
     字段缺失/类型错误被当成功（本仓 P1 形态，独立于 shim 也该修）
- **已失效的价值论证**：D3.2「360 向量驱动 shim 自测 = 兑现 M2 入口层证据」**不成立**——
  shim 调的仍是同一个 `build_request_payload`，只能验 HTTP 适配层没接错，
  不构成独立实现的交叉验证。M2 的入口层证据仍然悬空。
- **复活条件**：出现第二个非 Python 消费者，或阶段 0 证明就地修补不掉问题。
- **预期产出**：把 `LLMClient` 包成 `/v1/chat/completions` 端点，任何语言的 OpenAI SDK
  把 `base_url` 指过来即可用上全部五项能力（参数翻译 / JSON 自修正 / 内容审查降级 /
  重试退避 / CallTrace）。
- **当前范围**：已由 CEO review 定（2026-08-06）。核心 = FastAPI 应用 +
  `POST /v1/chat/completions` + 零持久化状态 + fail-open 带显式降级信号 +
  `stream:true` 显式 400；另加 `GET /v1/capabilities` 与 `GET /v1/models`（能力自省）、
  `/healthz` 含上游探活、翻译决策回显响应头、360 条向量驱动的 shim 自测。
  **明确不做**：有状态中心网关（虚拟 key / 预算 / 缓存）、流式支持。
- **关键决策**：
  - **提优先级的依据**：issue #13 中方案 A 的触发条件「有非 Python 项目明确需要
    JSON 自修正」**已经满足** —— AdCommentsPlatform `src/ai/analysis.ts:155` 正在用
    正则 `jsonMatch[0]` + `JSON.parse` 手搓 JSON 提取。
  - **消费侧接入成本已实测为「改一个环境变量」**：AdComments 的
    `src/config/env.ts:220` 把 `AI_API_URL_PRIMARY` 拼给 `openai` Node SDK，
    网关地址本来就是配置项。TS 代码零改动。
  - 消费侧唯一该动的代码：`task-config.ts` 的 `resolveThinkingPayload()`
    （模型名 → 字段形状映射）可删，改发 OpenAI 标准 `reasoning_effort`。
    但 `AI_TASK_THINKING_POLICY`（哪个任务该不该关思考）**必须保留** —— 那是业务
    决策不是 provider 差异，且有对照 eval 锁死（sentiment 关思考致 28% 中性误判）。
- **已知阻塞**：
  - **首要**：M3-阶段0 的测量结果（见上一节）
  - **BLOCKER-2**：`response_format:{json_schema}` 透传上游 vs 翻译成 `chat_json`。
    不是细节——`analysis.ts:40` 用的正是 `{type:"json_schema", strict:true}`，
    而 analysis 是三条入口层证据链路之一，不定这条连「完成」都定义不出来
  - **重试三层叠加**（架构级）：Bun SDK `maxRetries=2` × shim 内 `retry.py` ×
    New API 自身重试 = 乘法放大；且消费者 30s 超时后 shim 仍在重试，烧配额且无人接收。
    注意 `retry.py` 的 `_NO_RETRY_TYPES` 含 `TimeoutError`（不重试），
    与 SDK 对超时默认重试的行为相反，统一核算时必须算进去
  - fail-open 的覆盖边界：只保证异常路径，还是补翻译前后字段一致性自检？
    （本仓 P1 红线是静默出错，倾向后者）
  - `/v1/capabilities` 是否与 `probe_caps.py` 集成——caps 数据是**网关绑定**的，
    换网关需重验，静态转发不解决网关身份漂移
  - 依赖隔离：FastAPI/uvicorn 与「零重依赖（核心只依赖 httpx）」约束怎么共存
  - 鉴权：透传消费者 Authorization 给上游，还是 shim 自持 key
- **可靠性前提**：**回滚路径需新建**。`AI_API_URL_SECONDARY` 只在 `env.ts` 被解析、
  零消费侧引用，是死配置，不能当现成回滚开关（按 core.md「落地判据=消费侧有引用」）。
- **推进前必须拿到的证据**：
  - [ ] shim 跑通 `conformance.json` 全部 360 条向量；环境：本地；
        命令：待定（与 Python 侧共用同一份向量，这是 M2 的兑现方式）
  - [ ] **入口层证据**：AdCommentsPlatform 把 `AI_API_URL_PRIMARY` 指向 shim，
        translate / sentiment / analysis 三条真实链路跑通；环境：AdComments 开发环境；
        真实入口：该服务的评论处理流程（非单测、非 curl）
  - [ ] **对照 eval 重跑一致**：AdComments 现有 thinking 对照 eval 结论不变；
        环境：AdComments；入口：`scripts/eval-thinking-regression.ts`
- **完成条件**：三条证据齐 + AdComments 仓内 `resolveThinkingPayload()` 已删除

### M4：数据新鲜度与分发（原增量 4 剩余部分）

- **状态**：未开始
- **预期产出**：T5（字段级来源元数据 + 90 天陈旧告警）+ 分发形态收口。
- **当前范围**：
  - **T5 保留**：管的是 caps 数据的新鲜度，与消费形态无关，价值不受 M3 影响。
  - **T9 降级重定义**：原「Release asset + 版本化 URL」服务的是「消费者自己拉 JSON」
    这条路径，M3 落地后它降为兜底路径，紧迫性下降。改为「shim 镜像的版本化分发」
    才是新的分发主线。
- **关键决策**：T8（三态探针）已提前完成并合并（`5bed970`，PR #15），不在本里程碑。
- **已知阻塞**：依赖 M3 定下 shim 的部署形态，才能定镜像分发方式。
- **推进前必须拿到的证据**：
  - [ ] 人为把某 family 的 `verified_at` 改到 100 天前，CI 告警；环境：CI
  - [ ] 消费者按文档从版本化来源取到一次产物；环境：AdComments；真实入口：部署流程
- **完成条件**：待 M3 收口后细化

### ~~M-old：TS 参考实现（原增量 3，T6）~~ —— 已砍

- **状态**：**已砍（2026-08-06 审计决定）**
- **砍掉理由**：
  1. **范式错误**：属于「每语言独立 SDK」范式，是五种范式里唯一会让单人维护成本
     翻倍的一条。Python 侧 1023 个测试的可信度，TS 侧要重建一遍。
  2. **对唯一消费者是负收益**：AdComments 的 73 行硬表已覆盖它实际用到的全部场景
     （两族模型 / 二态 thinking / 不用 effort clamp 选邻），且有对照 eval 锁死。
     换成 TS 客户端换来同样行为，代价是迁移 + 重跑 eval。
  3. **不碰真实痛点**：它只能补齐「参数翻译」这 1/5，而消费者的痛点在手搓 JSON 提取
     和缺通用重试上 —— 这两项只有 M3 能给。
- **复活条件**：出现一个明确不愿引入 shim（不接受多一跳或多一个进程）、且需要
  provider 翻译的非 Python 消费者。届时重开评估，优先考虑让它直接读 `caps.json`
  （M2 的兜底路径）而不是新造 SDK。

## 发版节奏

原计划「四个增量齐了一次性发 `0.9.0`」（用户拍板，避免下游拿到中间态）。
增量 3 砍掉后该约束需重新评估：M2 已合并且零行为变更（`strict_unknown_models`
默认 `False`），不存在「已 breaking 但配套未到」的风险。**待定：是否 M3 落地前
先发 0.9.0。** 当前版本仍 `0.8.0`。

## 路线图审计

### 2026-08-06 / 增量 2 合并后（`ceb4e08`，PR #16）

- **审计日期 / 增量**：2026-08-06 / 增量 2（PR #16）
- **里程碑真完成了吗？**：M1/M2 在**库内自洽层面**完成，证据充分（1023 passed /
  mypy strict / 双导出物零漂移 / gate 主审 4 条 major 全修）。但 **M2 缺入口层证据** ——
  契约交付了，唯一消费者一行没用过：AdCommentsPlatform `src/ai/task-config.ts`
  仍是 73 行硬表，注释还指着 issue #13。按 core-lead「至少一条入口层证据」，
  M2 的 done 当时未兑现。
- **下一个目标还是对的吗？**：**不对，原增量 3（TS 参考实现）已砍。** 依据是消费侧
  实测数据 + 架构范式分析，详见上方 M-old 节的三条理由。
- **有没有漏掉的里程碑？**：**漏了一个** —— HTTP shim（issue #13 的方案 A）。它当时
  躺在 Deferred TODOS 标 P3，触发条件写的是「有非 Python 项目明确需要 content
  fallback / JSON 自修正 / CallTrace」，而该条件**已经满足**（AdComments
  `analysis.ts:155` 手搓 JSON 提取）。已提为 M3 并设为当前激活里程碑。
- **新证据是否改变了工作顺序？**：**改变三处**：
  1. 原「增量 3/4 互不依赖可并行」不成立 —— 分发形态是消费路径的下游决策，
     现已随主路径改变而重定义（见 M4）。
  2. 原 T7（TS CI 闸接入 gate.yml）**已被实测证伪** —— 本仓 `gate.yml` 只调用复用
     workflow，无本地步骤可插。该任务随增量 3 一并作废。
  3. 原计划的「先让 AdComments 改读 caps.json 拿入口层证据」**已作废**：既然
     shim 是主路径，入口层证据应由「经 shim 跑通真实调用」兑现，而不是让消费者
     去实现一条即将被取代的兜底路径。
- **done 的定义还成立吗？**：**不成立，已改**。原增量 3 的「`bun test` 全绿，与
  Python 同一份向量」是库函数绿，按 core-lead 不构成入口层证据；原 T9 的
  「pre-release asset 可取」是提供侧证据。M3/M4 的证据条目已按「真实入口 + 写明
  环境」重写。
- **审计结论**：**调整路线图**。变更四项：
  1. 新增 M3（HTTP shim），设为当前激活里程碑。
  2. 砍掉原增量 3（TS 参考实现），记录复活条件。
  3. M2 定位从「跨语言主路径」改为「兜底路径 + 一致性验证基座」，入口层证据条目
     顺延到 M3。
  4. M4 范围重定义：T5 保留，T9 降级为兜底路径分发，shim 镜像分发成为新主线。
  5. 发版节奏的「四增量齐了发 0.9.0」约束需重新评估。
