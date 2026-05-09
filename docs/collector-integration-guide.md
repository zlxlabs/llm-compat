# Collector 接入指南

下游项目接入 llm-compat-collector，实现拒绝事件自动上报 + 敏感词共享。

## 前提条件

- llm-compat-collector 服务已部署（当前运行在 n305 的 `127.0.0.1:8234`）
- 项目已使用 llm-compat 的 content fallback 功能
- 项目容器在 `llm-net` Docker 网络中

## 接入步骤

### 1. 确认 llm-compat 版本

确保使用最新版本（包含 collector 集成）：

```bash
uv add git+https://github.com/zj1123581321/llm-compat.git@main
```

### 2. 项目容器加入 llm-net 网络

在项目的 `docker-compose.yml` 中添加：

```yaml
services:
  your-app:
    # ... 已有配置 ...
    networks:
      - llm-net      # 新增

networks:
  llm-net:
    external: true    # 使用已存在的共享网络
```

如果网络不存在，先创建：

```bash
docker network create llm-net
```

### 3. 修改 LLMClient 初始化

在原有配置基础上，加三个参数：

```python
from llm_compat import LLMClient

async with LLMClient(
    base_url="https://your-newapi.com/v1",
    api_key="sk-xxx",

    # 已有的 fallback 配置（必须有，collector 依赖 fallback 触发上报）
    content_fallbacks={
        "deepseek-*": ["gpt-4.1-mini", "gemini-2.5-flash"],
        "qwen-*": ["gpt-4.1-mini"],
    },

    # ---- 新增：collector 集成 ----
    collector_url="http://llm-compat-collector:8000",
    collector_project="your-project-name",   # 标识来源，如 "video-api"
    collector_api_key="",                    # 与服务端 COLLECTOR_API_KEY 一致，未设则留空
) as client:
    result = await client.chat("deepseek-v4", messages)
```

同步客户端同理：

```python
from llm_compat import SyncLLMClient

with SyncLLMClient(
    base_url="...",
    api_key="...",
    content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
    collector_url="http://llm-compat-collector:8000",
    collector_project="your-project-name",
) as client:
    result = client.chat("deepseek-v4", messages)
```

### 4. 通过环境变量配置（推荐）

避免硬编码，用环境变量：

```python
import os

client = LLMClient(
    base_url=os.environ["LLM_BASE_URL"],
    api_key=os.environ["LLM_API_KEY"],
    content_fallbacks={"deepseek-*": ["gpt-4.1-mini"]},
    collector_url=os.environ.get("LLM_COLLECTOR_URL", ""),
    collector_project=os.environ.get("LLM_COLLECTOR_PROJECT", ""),
    collector_api_key=os.environ.get("LLM_COLLECTOR_API_KEY", ""),
)
```

项目 `.env` 文件：

```env
LLM_COLLECTOR_URL=http://llm-compat-collector:8000
LLM_COLLECTOR_PROJECT=your-project-name
LLM_COLLECTOR_API_KEY=
```

**注意**：`collector_url` 为空时不启用 collector，行为与之前完全一致。

## 工作原理

接入后的行为变化：

```
之前（无 collector）：
  请求 → deepseek 拒绝 → fallback 到 gpt-4.1-mini → 返回结果
                          ↑
                          这次拒绝的信息丢失了

之后（有 collector）：
  请求 → deepseek 拒绝 → fallback 到 gpt-4.1-mini → 返回结果
                          ↓
                          自动上报拒绝事件到 collector（fire-and-forget，不增加延迟）
```

### 上报内容

每次 fallback 触发时，自动上报 14 个字段：

| 字段 | 示例 | 说明 |
|------|------|------|
| `model` | `deepseek-v4` | 被拒绝的模型 |
| `provider` | `deepseek` | provider 族 |
| `source_project` | `video-api` | 你的项目标识 |
| `request_id` | `a1b2c3d4` | 关联日志 |
| `input_preview` | `"用户输入前200字..."` | 输入摘要 |
| `response_preview` | `"我无法回答..."` | 拒绝响应摘要 |
| `message_count` | `3` | 对话消息数 |
| `has_images` | `false` | 是否多模态 |
| `detection_layer` | `http_error` | 检测层 |
| `http_status` | `500` | HTTP 状态码 |
| `finish_reason` | `content_filter` | API finish_reason |
| `fallback_model` | `gpt-4.1-mini` | 最终成功的模型 |
| `fallback_chain` | `["deepseek-v4", "gpt-4.1-mini"]` | 完整尝试链 |
| `created_at` | `2026-05-09 15:30:00` | 服务端时间戳 |

### 降级保障

collector 不可用时（网络故障、服务重启等），上报会静默失败并写入本地缓存。**绝不影响主 chat 功能**。

| 场景 | 行为 | 影响 |
|------|------|------|
| collector 正常 | 实时上报 | 无 |
| collector 短暂不可用 | 写本地 JSONL 缓存 | 无 |
| collector 完全不存在 | 静默跳过 | 无 |
| collector_url 未配置 | 不启用 collector | 无 |

## 验证接入

### 1. 检查 collector 服务是否可达

从项目容器内：

```bash
docker exec your-container curl -s http://llm-compat-collector:8000/stats
```

应返回 JSON。如果连不上，检查：
- 容器是否在 `llm-net` 网络中：`docker network inspect llm-net`
- collector 是否在运行：`docker ps | grep llm-compat-collector`

### 2. 触发一次拒绝测试

```python
# 用一段已知会触发拒绝的内容测试
result = await client.chat("deepseek-v4", [
    {"role": "user", "content": "（已知的敏感内容）"}
])
print(result.fallback_from)  # 应该显示 "deepseek-v4"
```

### 3. 检查上报是否成功

```bash
curl http://localhost:8234/stats | python3 -m json.tool
```

应看到 `total_refusals` 增加，`recent_refusals` 中有刚才的记录。

## 日常运维

### 查看拒绝趋势

```bash
# 统计概览
curl -s http://localhost:8234/stats | python3 -m json.tool

# 只看最近拒绝事件
curl -s http://localhost:8234/stats | python3 -c "
import json, sys
data = json.load(sys.stdin)
for r in data['recent_refusals']:
    print(f\"{r['ts']}  {r['model']:20s}  {r['detection_layer']:18s}  {r['input_preview'][:50]}\")
"
```

### 审核后添加敏感词

```bash
# 添加词
curl -X POST http://localhost:8234/words \
  -H 'Content-Type: application/json' \
  -d '{"word": "新发现的敏感词"}'

# 查看当前词表
curl -s http://localhost:8234/words | python3 -m json.tool

# 删除误报词
curl -X DELETE http://localhost:8234/words/误报词
```

添加词后，各项目的 SensitiveDetector 会在下次热重载时自动加载（热重载功能待实现，当前需重启容器）。

## 常见问题

### Q: 不配 collector 会影响现有功能吗？

不会。`collector_url` 不传或传空字符串，行为与之前完全一致。collector 是纯增量功能。

### Q: collector 挂了会影响 chat 吗？

不会。上报是 fire-and-forget，失败静默跳过。collector 挂了 = 回到没有 collector 时的状态。

### Q: 多个项目怎么区分？

通过 `collector_project` 参数。每个项目传自己的名字，在 `/stats` 的 `recent_refusals` 中可以看到 `source_project` 字段。

### Q: input_preview 会泄露用户数据吗？

- collector 仅在 Docker 内网通信，不暴露公网
- 摘要默认截取前 200 字，可通过 `CollectorClient(preview_length=100)` 调整
- 如果完全不想传摘要，可以自行实现 CollectorClient 覆盖 `extract_preview` 方法返回空字符串

### Q: 需要改业务代码逻辑吗？

不需要。只改 `LLMClient` 初始化参数，`chat()` / `chat_json()` / `chat_image()` 调用方式不变。
