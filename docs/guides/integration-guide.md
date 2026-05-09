# llm-compat 从零接入指南

从零开始接入 llm-compat，获得：多 provider 适配、内容审查自动降级、敏感词积累。

## 你现在的代码可能长这样

```python
import httpx

async with httpx.AsyncClient(base_url="https://your-newapi.com/v1") as client:
    resp = await client.post(
        "/chat/completions",
        headers={"Authorization": "Bearer sk-xxx"},
        json={
            "model": "deepseek-v4",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
```

问题：
- 切模型时 thinking/reasoning_effort 参数各家写法不同
- deepseek 因内容审查返回 500，需要手动切换到海外模型
- JSON 输出格式不一致（code fence、bare list）
- 重试、超时、日志每个项目都在重复写

## 接入后的代码

```python
from llm_compat import LLMClient

async with LLMClient(
    base_url="https://your-newapi.com/v1",
    api_key="sk-xxx",
) as client:
    result = await client.chat("deepseek-v4", [{"role": "user", "content": "hello"}])
    print(result)              # 直接当 str 用
    print(result.usage)        # TokenUsage(prompt=8, completion=75, total=83)
    print(result.latency_ms)   # 1519
```

下面分三步走，每一步都是独立可用的，可以按需停下。

---

## 第一步：基础接入

### 1.1 安装

```bash
uv add git+https://github.com/zj1123581321/llm-compat.git
```

### 1.2 替换 HTTP 调用

**异步（推荐）：**

```python
from llm_compat import LLMClient

async with LLMClient(
    base_url="https://your-newapi.com/v1",
    api_key="sk-xxx",
) as client:
    # 基础对话
    result = await client.chat(
        "deepseek-v4",
        [{"role": "user", "content": "hello"}],
        reasoning_effort="high",  # 自动翻译为各 provider 格式
    )
    print(result)              # str
    print(result.usage)        # TokenUsage
    print(result.latency_ms)   # int
    print(result.request_id)   # str

    # JSON 输出（自动清洗 code fence、bare list）
    from pydantic import BaseModel

    class TagResult(BaseModel):
        tags: list[str]

    result = await client.chat_json(
        "gpt-4.1-mini",
        [{"role": "user", "content": "给 Python 打 3 个标签"}],
        schema=TagResult,
    )
    print(result.parsed)  # TagResult(tags=['编程语言', 'Python', '开发'])

    # 流式输出
    async for chunk in client.chat_stream("deepseek-v4", messages):
        print(chunk, end="")

    # 图片理解
    result = await client.chat_image(
        "gpt-4o", "描述这张图",
        image_data=raw_bytes, media_type="image/png",
    )
```

**同步：**

```python
from llm_compat import SyncLLMClient

with SyncLLMClient(base_url="...", api_key="...") as client:
    result = client.chat("gpt-4.1-mini", messages)
```

### 1.3 错误处理

```python
from llm_compat import FatalError, TimeoutError, JSONParseError

try:
    result = await client.chat_json("gpt-4o", messages, schema=MyModel)
except JSONParseError as e:
    print(e.raw_content)   # 模型返回的原始内容
except TimeoutError:
    pass  # 不会重试（同样的输入大概率同样超时）
except FatalError:
    pass  # 401/403/404，不会重试
```

### 到这一步你获得了

- 自动重试（指数退避，可配置）
- 结构化日志（标准 logging，带 request_id）
- reasoning_effort 跨 provider 翻译（deepseek/gemini/openai 写法不同）
- JSON 输出清洗（code fence 剥离、Pydantic 校验）
- 统一的错误层级

---

## 第二步：内容审查自动降级

国内模型（DeepSeek、Qwen 等）因内容审查拒绝回答时，自动切换到海外模型。

### 2.1 配置 fallback 链

```python
async with LLMClient(
    base_url="https://your-newapi.com/v1",
    api_key="sk-xxx",
    content_fallbacks={                                    # 新增
        "deepseek-v4-pro": ["gemini-3-flash-preview", "gemini-2.5-flash"],
        "deepseek-v4-flash": ["gemini-3.1-flash-lite-preview"],
    },
) as client:
    result = await client.chat("deepseek-v4-pro", messages)
    # deepseek-v4-pro 被拒绝 → 自动尝试 gemini-3-flash-preview → 再不行尝试 gemini-2.5-flash
    print(result.model)          # 实际使用的模型
    print(result.fallback_from)  # 原始模型（未降级时为 None）
```

### 2.2 工作机制

拒绝检测三层（自动，无需配置）：

1. **结构化信号**：`finish_reason=content_filter`、空 `choices`、`refusal` 字段
2. **HTTP 错误码**：400/403/451/500 + body 包含 `sensitive_words`、`content_policy` 等
3. **响应文本关键词**：内置中英文拒绝关键词列表（`我无法回答`、`I cannot assist` 等）

### 2.3 图片请求自动跳过不支持的模型

```python
# deepseek 不支持 vision，fallback 链中只会尝试支持 vision 的模型
result = await client.chat_image(
    "deepseek-v4", "描述这张图",
    image_data=img, media_type="image/png",
)
```

### 2.4 前置敏感词检测（可选）

如果你已有已知的敏感词列表，可以在发送前检测，直接跳过主模型：

```bash
# 安装可选依赖，启用高性能匹配
uv add "git+https://github.com/zj1123581321/llm-compat.git[sensitive]"
```

```python
from llm_compat.sensitive import SensitiveDetector

detector = SensitiveDetector(words=["敏感词1", "敏感词2"])
client = LLMClient(
    ...,
    content_fallbacks={"deepseek-v4-pro": ["gemini-3-flash-preview", "gemini-2.5-flash"]},
    sensitive_detector=detector,                            # 新增
)
# 输入包含敏感词时，直接用 fallback 模型，省一次 API 调用
```

### 2.5 所有模型都拒绝时

```python
from llm_compat import ContentPolicyError

try:
    result = await client.chat("deepseek-v4-pro", messages)
except ContentPolicyError as e:
    print(e.attempted_models)  # ['deepseek-v4-pro', 'gemini-3-flash-preview', 'gemini-2.5-flash']
    print(e.raw_content)       # 最后一个模型的拒绝内容
    print(e.original_model)    # 'deepseek-v4-pro'
```

### 2.6 从 URL 加载拒绝关键词（可选）

内置的拒绝关键词列表（`我无法回答`、`I cannot assist` 等）覆盖有限。可以从 URL 动态加载更多关键词，扩大响应端拒绝检测覆盖面：

```python
client = LLMClient(
    ...,
    content_fallbacks={"deepseek-v4-pro": ["gemini-3-flash-preview"]},
    # 从 URL 加载拒绝关键词（支持多个 URL）
    refusal_keywords_url=[
        "http://llm-compat-collector:8000/words",   # Collector 积累的
        "https://cdn.internal/shared-keywords.json", # 团队共享的
    ],
    # 手动补充（可选，与 URL 来源合并去重）
    refusal_keywords=["项目专属拒绝词"],
)
```

特性：
- 支持 `str`（单个 URL）或 `list[str]`（多个 URL）
- 同进程多 LLMClient 实例共享缓存，不重复拉取
- 后台每 5 分钟自动刷新，`chat()` 每次调用读最新词表
- URL 不可达时保留旧缓存，永不丢词
- 手动词 + 所有 URL 词自动去重合并

### 到这一步你获得了

- 内容审查被拒时自动切换到海外模型
- 三层拒绝检测（结构化信号 → HTTP 错误 → 关键词）
- 图片请求自动跳过不支持 vision 的 fallback 模型
- 可选的前置敏感词检测（已知敏感词直接跳过，省 API 调用）
- 可选的 URL 动态关键词（拒绝检测覆盖面随 Collector 积累自动扩大）

---

## 第三步：接入 Collector（敏感词积累）

Collector 是一个独立的 Sidecar 服务，自动收集所有项目的拒绝事件，人工审核后提取敏感词，反馈回所有项目的 pre-scan。

```
项目 A ──┐                                    ┌── 项目 A 加载新词表
项目 B ──┼── 拒绝事件上报 → Collector → 人工审核 ──┼── 项目 B 加载新词表
项目 C ──┘                  (SQLite)    加词    └── 项目 C 加载新词表
```

### 3.1 前提：Collector 服务已部署

Collector 是独立部署的（不在你的项目里），部署方式见仓库的 `collector/` 目录。如果你的团队已经部署好了，跳到 3.2。

### 3.2 项目容器加入 llm-net 网络

Collector 和你的项目需要在同一个 Docker 网络中通信：

```yaml
# 你的项目 docker-compose.yml
services:
  your-app:
    # ... 已有配置 ...
    networks:
      - llm-net               # 新增

networks:
  llm-net:
    external: true             # 使用已存在的共享网络
```

### 3.3 添加 Collector 参数

在第二步的基础上，加三个参数：

```python
async with LLMClient(
    base_url="https://your-newapi.com/v1",
    api_key="sk-xxx",
    content_fallbacks={
        "deepseek-v4-pro": ["gemini-3-flash-preview", "gemini-2.5-flash"],
        "deepseek-v4-flash": ["gemini-3.1-flash-lite-preview"],
    },
    # ---- 新增：Collector 集成 ----
    collector_url="http://llm-compat-collector:8000",    # 拒绝事件上报
    collector_project="your-project-name",               # 来源标识，如 "video-api"
    collector_api_key="",                                # 与 COLLECTOR_API_KEY 一致
    # Collector 词表作为拒绝关键词来源（动态更新）
    refusal_keywords_url="http://llm-compat-collector:8000/words",
) as client:
    result = await client.chat("deepseek-v4-pro", messages)
    # fallback 触发时自动上报 + 词表动态加载
```

### 3.4 推荐：用环境变量配置

```python
import os

client = LLMClient(
    base_url=os.environ["LLM_BASE_URL"],
    api_key=os.environ["LLM_API_KEY"],
    content_fallbacks={
        "deepseek-v4-pro": ["gemini-3-flash-preview", "gemini-2.5-flash"],
        "deepseek-v4-flash": ["gemini-3.1-flash-lite-preview"],
    },
    collector_url=os.environ.get("LLM_COLLECTOR_URL", ""),
    collector_project=os.environ.get("LLM_COLLECTOR_PROJECT", ""),
    collector_api_key=os.environ.get("LLM_COLLECTOR_API_KEY", ""),
    refusal_keywords_url=os.environ.get("LLM_REFUSAL_KEYWORDS_URL", ""),
)
```

项目 `.env`：

```env
LLM_BASE_URL=https://your-newapi.com/v1
LLM_API_KEY=sk-xxx
LLM_COLLECTOR_URL=http://llm-compat-collector:8000
LLM_COLLECTOR_PROJECT=your-project-name
LLM_COLLECTOR_API_KEY=
LLM_REFUSAL_KEYWORDS_URL=http://llm-compat-collector:8000/words
```

### 3.5 验证接入

**检查网络连通性**（从项目容器内）：

```bash
docker exec your-container curl -s http://llm-compat-collector:8000/stats
# 应返回 JSON：{"total_refusals": 0, "word_count": 0, ...}
```

连不上？检查：
- `docker network inspect llm-net` 确认两个容器都在
- `docker ps | grep llm-compat-collector` 确认 Collector 在运行

**触发拒绝测试**：

```python
result = await client.chat("deepseek-v4", [
    {"role": "user", "content": "（已知的敏感内容）"}
])
print(result.fallback_from)  # 应显示 "deepseek-v4"
```

**确认上报成功**：

```bash
curl -s http://localhost:8234/stats | python3 -m json.tool
# total_refusals 应该增加
```

### 3.6 上报了什么

每次 fallback 触发时自动上报，**fire-and-forget（不增加延迟）**：

| 字段 | 示例 | 用途 |
|------|------|------|
| `model` | `deepseek-v4` | 哪个模型拒绝了 |
| `provider` | `deepseek` | provider 族 |
| `source_project` | `video-api` | 哪个项目 |
| `request_id` | `a1b2c3d4` | 关联日志 |
| `input_preview` | `"前200字..."` | 提取敏感词用 |
| `response_preview` | `"我无法回答..."` | 了解拒绝原因 |
| `detection_layer` | `http_error` | 哪层检测到的 |
| `http_status` | `500` | HTTP 状态码 |
| `fallback_model` | `gemini-3-flash-preview` | 谁兜住了 |
| `fallback_chain` | `["deepseek-v4-pro", "gemini-3-flash-preview"]` | 完整尝试链 |
| + 4 个辅助字段 | | message_count, has_images, finish_reason, created_at |

### 3.7 降级保障

Collector 的任何故障都**不影响** chat 功能：

| 场景 | 行为 |
|------|------|
| Collector 正常 | 实时上报 |
| Collector 短暂不可用 | 写本地 JSONL 缓存，恢复后上传 |
| Collector 完全不存在 | 静默跳过 |
| `collector_url` 未配置 | 不启用，行为与第二步完全一致 |

### 到这一步你获得了

- 所有项目的拒绝事件自动汇聚到一处
- 人工审核后加词，所有项目共享词表
- 系统越用越聪明：新发现的敏感模式不断积累

---

## 日常运维

### 查看拒绝统计

```bash
curl -s http://localhost:8234/stats | python3 -m json.tool
```

### 审核并添加敏感词

```bash
# 查看最近拒绝事件
curl -s http://localhost:8234/stats | python3 -c "
import json, sys
for r in json.load(sys.stdin)['recent_refusals']:
    print(f\"{r['ts']}  {r['model']:15s}  {r['detection_layer']:18s}  {r['input_preview'][:60]}\")
"

# 发现规律后加词
curl -X POST http://localhost:8234/words \
  -H 'Content-Type: application/json' \
  -d '{"word": "敏感词"}'

# 查看当前词表
curl -s http://localhost:8234/words | python3 -m json.tool

# 删除误报词
curl -X DELETE http://localhost:8234/words/误报词
```

---

## 完整配置示例

```python
import os
from llm_compat import LLMClient
from llm_compat.sensitive import SensitiveDetector

# 可选：本地已知敏感词
detector = SensitiveDetector(words=["已知敏感词1", "已知敏感词2"])

async with LLMClient(
    # 基础配置
    base_url=os.environ["LLM_BASE_URL"],
    api_key=os.environ["LLM_API_KEY"],
    max_retries=3,
    total_timeout=300.0,

    # 内容审查降级
    content_fallbacks={
        "deepseek-v4-pro": ["gemini-3-flash-preview", "gemini-2.5-flash"],
        "deepseek-v4-flash": ["gemini-3.1-flash-lite-preview"],
    },
    sensitive_detector=detector,

    # Collector 集成（全部可选，不配就不启用）
    collector_url=os.environ.get("LLM_COLLECTOR_URL", ""),
    collector_project=os.environ.get("LLM_COLLECTOR_PROJECT", ""),
    collector_api_key=os.environ.get("LLM_COLLECTOR_API_KEY", ""),
    # 动态关键词加载（可选，从 Collector 或其他 URL 加载）
    refusal_keywords_url=os.environ.get("LLM_REFUSAL_KEYWORDS_URL", ""),
) as client:
    result = await client.chat("deepseek-v4-pro", messages)
```

---

## 常见问题

### Q: 可以只用第一步不用后面的吗？

可以。每一步都是独立的。第一步 = 统一的 LLM 客户端。第二步加降级。第三步加积累。按需停下。

### Q: collector_url 不配会怎样？

不启用 Collector，行为与第二步完全一致。Collector 是纯增量功能。

### Q: Collector 挂了会影响 chat 吗？

不会。上报是 fire-and-forget，Collector 挂了 = 回到没有 Collector 时的状态。

### Q: 需要改业务逻辑吗？

不需要。只改 `LLMClient` 初始化参数。`chat()` / `chat_json()` / `chat_image()` / `chat_stream()` 调用方式不变，返回类型不变。

### Q: 多个项目怎么区分拒绝来源？

通过 `collector_project` 参数。每个项目传自己的名字（如 `"video-api"`、`"memos-backend"`），Collector 的 `/stats` 中可看到 `source_project` 字段。

### Q: input_preview 会泄露用户数据吗？

Collector 仅在 Docker 内网通信，不暴露公网。摘要默认截取前 200 字，可配置长度。

---

## 附录 A：已有项目迁移

如果你的项目已有自建的 LLM 客户端（直接用 httpx + 自己的重试/翻译逻辑），接入 llm-compat 时可以删除这些重复代码。

### 迁移策略

llm-compat 接管 **HTTP 通信、重试、provider 翻译、JSON 清洗**，项目只保留 **配置解析 + Prompt 模板 + 业务逻辑**。

```
迁移前:  项目代码 = 配置 + Prompt + HTTP + 重试 + 翻译 + JSON清洗 + 业务
迁移后:  项目代码 = 配置 + Prompt + 业务
         llm-compat = HTTP + 重试 + 翻译 + JSON清洗
```

### 可以删除的代码

| 功能 | 典型代码 | llm-compat 替代 |
|------|---------|-----------------|
| httpx 客户端管理 | `httpx.AsyncClient(...)` | `LLMClient` 内部管理 |
| 重试逻辑 | `for attempt in range(max_retries)` | `retry.py` 自动处理 |
| 退避计算 | `_compute_backoff()` | 内置指数退避+jitter |
| 可重试判断 | `_is_retryable()` | `classify_error()` |
| Retry-After 处理 | `_retry_after_seconds()` | 内置 |
| Provider 翻译 | `if "deepseek" in model` | `providers.py` 自动检测 |
| JSON code fence 清洗 | `re.search(r'```json')` | `json_utils.py` |
| base64 图片编码 | `_build_image_content()` | `chat_image()` 内置 |
| 请求 ID 生成 | `uuid.uuid4()` | 自动生成 |
| token 用量提取 | `usage["prompt_tokens"]` | `ChatResult.usage` |

### 薄封装模式

项目级 LLMClient 变成 llm-compat 的薄封装，只保留配置解析：

```python
# 迁移后 (~100 行，从 ~538 行精简)
from llm_compat import LLMClient as BaseLLMClient, ChatResult

class LLMClient:
    """项目级封装：配置解析 + task_overrides"""

    def __init__(self, config: LLMConfig):
        self._base = BaseLLMClient(
            base_url=config.text.base_url,
            api_key=config.text.api_key,
            content_fallbacks={"deepseek-v4-pro": ["gemini-3-flash-preview", "gemini-2.5-flash"]},
            collector_url=os.environ.get("LLM_COLLECTOR_URL", ""),
            collector_project="my-project",
        )
        self._config = config

    def _resolve_config(self, task_name, use_image=False):
        # 保留：项目特有的配置解析逻辑
        ...

    async def chat(self, task_name, messages, *, use_image=False) -> ChatResult:
        cfg = self._resolve_config(task_name, use_image)
        return await self._base.chat(
            model=cfg.model,
            messages=messages,
            reasoning_effort=cfg.reasoning_effort,
        )

    async def close(self):
        await self._base.close()
```

### reasoning_effort 迁移

旧值 `"none"` 改为 `"disabled"`：

```yaml
# 迁移前
reasoning_effort: "none"     # 意图是关闭思考，但 DeepSeek 会忽略

# 迁移后
reasoning_effort: "disabled" # 明确关闭，DeepSeek → thinking.type=disabled
```

### 自定义 Provider 映射

如果 New API 代理重命名了模型名：

```python
from llm_compat import register_provider, set_custom_patterns

register_provider("my-proxy-ds-*", "deepseek")
set_custom_patterns({"my-ds-*": "deepseek", "my-gpt-*": "openai_gpt4"})
```

### 迁移检查清单

- [ ] 安装 llm-compat
- [ ] 创建薄封装 LLMClient（保留配置解析，删除 HTTP/重试/翻译）
- [ ] `reasoning_effort: "none"` → `"disabled"`
- [ ] 删除重试逻辑代码
- [ ] 删除 JSON code fence 清洗代码
- [ ] 删除 base64 图片编码辅助函数
- [ ] 更新错误处理为 FatalError/TimeoutError/JSONParseError
- [ ] 可选：添加 validate_config 启动校验
- [ ] 可选：添加 content_fallbacks + collector 集成
- [ ] 运行测试验证
