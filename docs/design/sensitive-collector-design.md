# 敏感词积累系统设计

> **实现状态 (v0.5.0)**：设计文档中提到的 `SensitiveDetector.from_url()` / `reload()` 方案未采用。实际实现使用 `LLMClient(sensitive_words_url=...)` 参数，通过 `_keyword_cache.py` 统一管理 URL 加载 + 300s 轮询 + 版本计数器驱动的懒重建。Collector 新增 `/words.txt` 纯文本端点供消费。

## 概述

通过 Sidecar API 服务（collector）自动收集所有项目的拒绝事件，人工审核后提取敏感词，喂回 SensitiveDetector 实现前置检测闭环。

## 决策记录

| # | 决策 | 选择 | 原因 |
|---|------|------|------|
| D1 | 存储方案 | Sidecar API 服务 | Docker 环境下自然，比共享 volume 更干净 |
| D3 | 词表热重载 | 加入 | 实现简单，新词分钟级生效 |
| D4 | 自动候选词提取 | 延后 | 需分词依赖，先积累数据 |
| D5 | 统计看板 UI | 延后 | 初期 GET /stats JSON 够用 |
| D6 | 降级缓存 | 加入 | 符合库的降级哲学 |
| D7 | 输入摘要 | 含前 200 字 | 无摘要则无法学习 |
| D8 | 上报模式 | Fire-and-forget | 不影响主路径延迟 |

## 系统架构

```
┌──────────────────────────────────────────────────────┐
│                   宿主机 (Docker)                      │
│                                                       │
│  ┌──────────┐  ┌──────────┐       ┌──────────────┐   │
│  │ 项目 A   │  │ 项目 B   │  ...  │ 项目 N       │   │
│  │ LLMClient│  │ LLMClient│       │ LLMClient    │   │
│  └────┬─────┘  └────┬─────┘       └────┬─────────┘   │
│       │              │                  │              │
│       │    Docker Network (内网)         │              │
│       ▼              ▼                  ▼              │
│  ┌─────────────────────────────────────────────┐      │
│  │     llm-compat-collector (FastAPI + SQLite) │      │
│  │                                             │      │
│  │  POST /refusals     ← 拒绝事件上报          │      │
│  │  GET  /words        ← 当前词表              │      │
│  │  GET  /words/hash   ← 变更检测 (ETag)       │      │
│  │  GET  /stats        ← 统计 JSON             │      │
│  │  POST /words        ← 人工添加词             │      │
│  │  DELETE /words/{w}  ← 删除误报词             │      │
│  └─────────────────────────────────────────────┘      │
└───────────────────────────────────────────────────────┘
```

## 数据流

### 闭环：拒绝 → 记录 → 学习 → 预防

```
1. 请求被拒绝（fallback 触发）
   │
   ▼
2. llm-compat fire-and-forget 上报
   POST /refusals {model, error_type, input_preview, source_project, ts}
   │                            │
   │  collector 可用             │  collector 不可用
   ▼                            ▼
3a. 写入 SQLite              3b. 写入本地 JSONL 缓存
                                  │
                                  │ collector 恢复后
                                  ▼
                              3c. 自动上传缓存内容
   │
   ▼
4. 人工审核拒绝日志
   POST /words {"word": "xxx"}
   │
   ▼
5. 词表更新 → /words/hash 变化
   │
   ▼
6. 各项目后台轮询检测到 hash 变更
   │
   ▼
7. 重建 SensitiveDetector（热重载）
   │
   ▼
8. 下次相同模式 → pre-scan 命中 → 跳过主模型 → 省一次 API 调用
```

### 四条数据路径

```
  INPUT (拒绝事件)
    │
    ├─ HAPPY: collector 可用 → SQLite → 审核 → 词表
    ├─ NIL:   input_preview 为空 → 仍记录元数据
    ├─ ERROR: collector 不可用 → 本地 JSONL → 恢复后上传
    └─ EDGE:  collector 返回 5xx → 同 ERROR 路径
```

## Collector 服务设计

### 技术栈

- FastAPI (异步, 轻量)
- SQLite (零运维, 单文件)
- Docker 部署

### 数据模型

```sql
-- 拒绝事件
CREATE TABLE refusals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT NOT NULL,
    error_type TEXT NOT NULL,       -- "sensitive_words_detected", "content_filter", "refusal_keyword"
    input_preview TEXT DEFAULT '',   -- 前 200 字
    source_project TEXT DEFAULT '',  -- 来源项目标识
    provider TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 审核后的敏感词
CREATE TABLE words (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word TEXT NOT NULL UNIQUE,
    source TEXT DEFAULT 'manual',   -- "manual" | "extracted"
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    -- hit_count: Phase 2 再加 (ALTER TABLE ADD COLUMN)
);

-- 词表版本追踪
-- hash = SHA256(sorted(words).join("\n"))[:16]
CREATE TABLE word_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hash TEXT NOT NULL,
    word_count INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_refusals_created ON refusals(created_at);
CREATE INDEX idx_refusals_model ON refusals(model);
```

### API 规格

```
POST /refusals
  Body: {model, error_type, input_preview?, source_project?, provider?}
  Response: 201 Created
  幂等: 不需要（事件流天然允许重复）

GET /words
  Response: {"words": ["word1", "word2", ...], "hash": "abc123", "count": 42}
  客户端用 /words/hash 轻量轮询检测变更，变更时再调 /words 拉取全量

GET /words/hash
  Response: {"hash": "abc123"}
  用途: 轻量变更检测

POST /words
  Body: {word}
  Response: 201 Created | 409 Conflict (already exists)
  副作用: 更新 word_versions hash

DELETE /words/{word}
  Response: 204 No Content | 404 Not Found
  副作用: 更新 word_versions hash

GET /stats
  Response: {
    total_refusals, refusals_today, refusals_by_model,
    word_count, recent_refusals: [{model, error_type, input_preview, ts}]
  }
```

## llm-compat 库变更

### 新增参数

```python
client = LLMClient(
    base_url="...",
    api_key="...",
    content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
    # 新增: collector 集成
    collector_url="http://llm-compat-collector:8000",  # 可选
    collector_project="my-project",                     # 来源标识
    input_preview_length=200,                           # 摘要截取长度
)
```

### 变更的文件

| 文件 | 变更 |
|------|------|
| `_base.py` | `__init__` 新增 collector 参数; `_chat_orchestrator` 在 fallback 时触发上报 |
| `sensitive.py` | 新增 `from_url()` 类方法; 新增 `reload()` 方法 |
| `_collector.py` (新) | CollectorClient: 上报/拉取/降级缓存/热重载 |
| `__init__.py` | 导出 CollectorClient |

### CollectorClient 设计

```python
class CollectorClient:
    def __init__(
        self,
        url: str,
        project: str = "",
        preview_length: int = 200,
        poll_interval: int = 300,        # 热重载轮询间隔（秒）
        cache_dir: str | None = None,    # 降级缓存目录
    ): ...

    async def report_refusal(
        self,
        model: str,
        error_type: str,
        input_text: str,
        provider: str = "",
    ) -> None:
        """Fire-and-forget 上报。失败时写本地缓存。"""

    async def fetch_words(self) -> list[str]:
        """拉取词表。失败时返回缓存版本。"""

    async def check_update(self) -> bool:
        """检查词表是否有更新（对比 hash）。"""

    async def _upload_cached(self) -> None:
        """上传本地缓存的拒绝事件。"""

    def start_polling(self) -> None:
        """启动后台热重载轮询线程。"""

    def stop_polling(self) -> None:
        """停止轮询。"""
```

### _chat_orchestrator 集成点

在 `_base.py` 的 fallback 路径中，拒绝被检测到时：

```python
# 在 fallback loop 内，detect_refusal 返回 True 后:
if self._collector:
    # messages_to_text: 取所有 role=user 的 content 拼接，截取前 preview_length 字符
    preview = self._collector.extract_preview(messages)
    # fire-and-forget: 不阻塞主流程
    asyncio.create_task(self._collector.report_refusal(
        model=model,
        error_type="refusal_detected",
        input_text=preview,
        provider=provider,
    ))
```

**热重载启动时机**: `LLMClient` 在首次 `chat()` 调用时，检查 `_collector` 是否已启动 polling，未启动则通过 `asyncio.create_task` 启动 polling coroutine（而非线程，避免 thread-vs-event-loop 问题）。`SyncLLMClient` 使用后台线程 + `httpx.Client` 同步拉取。

**缓存上传时机**: 每次 `report_refusal` 成功时，顺带检查本地缓存是否有待上传内容。成功上传的条目即清理，失败条目保留等待下次重试。

## Error & Rescue Map

```
方法/路径                    | 失败场景                | 处理           | 用户影响
-----------------------------|------------------------|----------------|------------------
CollectorClient.report()     | collector 不可用        | 写本地 JSONL    | 无（fire-and-forget）
CollectorClient.report()     | 本地缓存目录不可写      | log warning     | 丢失该条记录
CollectorClient.fetch_words()| collector 不可用        | 用缓存词表      | 用旧词表，不影响功能
CollectorClient.fetch_words()| 首次启动且无缓存        | 返回空列表      | 仅用手动词表
CollectorClient.check_update | collector 不可用        | 保持当前词表    | 无
CollectorClient._upload_cache| 部分上传失败            | 保留失败条目    | 下次重试
热重载线程                   | 异常                   | log + 继续轮询  | 词表不更新
Collector POST /refusals     | SQLite 写入失败         | 500 + log      | 客户端走降级
Collector GET /words         | SQLite 读取失败         | 500 + log      | 客户端用缓存
```

**关键原则: collector 的任何故障都不影响 llm-compat 的核心 chat 功能。**

## 安全考虑

| 威胁 | 风险 | 缓解 |
|------|------|------|
| input_preview 含敏感内容 | 低 | 私有部署 + Docker 内网 + 可配置截取长度 |
| collector API 无认证 | 低 | 仅 Docker 内网可访问，不暴露公网 |
| SQLite 文件权限 | 低 | Docker volume 标准权限 |
| 缓存 JSONL 含敏感内容 | 低 | 存储在容器内部，collector 恢复后上传成功即清理，失败条目保留重试 |
| 词表投毒（恶意加词导致误跳过） | 中 | 人工审核 POST /words，不自动提取 |

## 部署

### docker-compose.yml (collector)

```yaml
services:
  llm-compat-collector:
    build: ./collector
    ports:
      - "127.0.0.1:8234:8000"    # 仅本机访问
    volumes:
      - collector-data:/data
    restart: unless-stopped
    networks:
      - llm-net

volumes:
  collector-data:

networks:
  llm-net:
    external: true               # 各项目共享的 Docker 网络
```

### 各项目 docker-compose 加入同一网络

```yaml
networks:
  llm-net:
    external: true
```

### 环境变量支持

```
LLM_COMPAT_COLLECTOR_URL=http://llm-compat-collector:8000
LLM_COMPAT_COLLECTOR_PROJECT=my-project
```

## 实现分期

### Phase 1: 核心闭环（本次）
- [ ] Collector 服务: POST /refusals, GET /words, GET /words/hash, POST /words, GET /stats
- [ ] llm-compat: CollectorClient + _base.py 集成 + SensitiveDetector.reload()
- [ ] 降级缓存: 本地 JSONL + 恢复上传
- [ ] 热重载: 后台轮询 + hash 检测
- [ ] 测试: collector mock + 端到端
- [ ] Docker 部署配置

### Phase 2: 智能化（延后）
- [ ] 自动候选词提取（n-gram 统计 + 人工审核）
- [ ] 置信度评分（命中率追踪）
- [ ] 统计看板 Web UI

### Phase 3: 规模化（远期）
- [ ] 跨机器同步
- [ ] 词表分组（按 provider/场景）
- [ ] API 认证（扩展到多用户场景）

## NOT in scope
- 自动提取算法（延后到 Phase 2，先积累数据）
- 统计看板 UI（延后，GET /stats JSON 足够）
- 跨机器同步（当前所有项目同一台机器）
- New API 代理层集成（不依赖特定代理实现）
- 词表分类/权重（初期扁平列表足够）
