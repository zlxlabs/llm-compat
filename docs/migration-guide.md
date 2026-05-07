# 下游项目迁移指南

## 安装

```bash
uv add git+https://github.com/zj1123581321/llm-compat.git
```

## 迁移策略

llm-compat 接管了 **HTTP 通信、重试、provider 翻译、JSON 清洗**，项目只保留 **配置解析 + Prompt 模板 + 业务逻辑**。

```
迁移前:  项目代码 = 配置 + Prompt + HTTP + 重试 + 翻译 + JSON清洗 + 业务
迁移后:  项目代码 = 配置 + Prompt + 业务
         llm-compat = HTTP + 重试 + 翻译 + JSON清洗
```

## 可以删除的代码

迁移后以下代码可以从项目中移除：

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

## 迁移步骤

### 第 1 步：安装 + 创建薄封装

项目级 LLMClient 变成 llm-compat 的薄封装，只保留配置解析逻辑：

```python
# 迁移前 (Memos Auto 风格, ~538 行)
import httpx

class LLMClient:
    def __init__(self, config: LLMConfig):
        self._config = config
        self._http_clients: dict[str, httpx.AsyncClient] = {}

    def _build_payload(self, cfg, messages, reasoning_effort):
        payload = {"model": cfg.model, "messages": messages}
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort  # 无法关闭思考!
        return payload

    async def _request_with_retry(self, cfg, payload):
        # 50 行重试逻辑...

    async def chat(self, task_name, messages, use_image=False):
        cfg = self._resolve_config(task_name, use_image)
        payload = self._build_payload(cfg, messages, cfg.reasoning_effort)
        # 发请求、处理响应、提取内容...
```

```python
# 迁移后 (~100 行)
from llm_compat import LLMClient as BaseLLMClient, ChatResult

class LLMClient:
    """项目级封装：配置解析 + task_overrides"""

    def __init__(self, config: LLMConfig):
        self._base = BaseLLMClient(
            base_url=config.text.base_url,
            api_key=config.text.api_key,
        )
        self._config = config

    def _resolve_config(self, task_name, use_image=False):
        # 保留：这是项目特有的配置解析逻辑
        cfg = self._config.image if use_image else self._config.text
        if task_name and task_name in self._config.task_overrides:
            # 合并 task 级覆盖
            ...
        return cfg

    async def chat(self, task_name, messages, *, use_image=False) -> ChatResult:
        cfg = self._resolve_config(task_name, use_image)
        return await self._base.chat(
            model=cfg.model,
            messages=messages,
            reasoning_effort=cfg.reasoning_effort,  # "disabled" 现在能正确翻译
        )

    async def chat_json(self, task_name, messages, schema):
        cfg = self._resolve_config(task_name)
        return await self._base.chat_json(
            model=cfg.model,
            messages=messages,
            schema=schema,
            reasoning_effort=cfg.reasoning_effort,
        )

    async def chat_stream(self, task_name, messages):
        cfg = self._resolve_config(task_name)
        async for chunk in self._base.chat_stream(
            model=cfg.model,
            messages=messages,
            reasoning_effort=cfg.reasoning_effort,
        ):
            yield chunk

    async def chat_image(self, text, image_data, media_type):
        cfg = self._resolve_config(None, use_image=True)
        return await self._base.chat_image(
            model=cfg.model,
            text=text,
            image_data=image_data,
            media_type=media_type,
        )

    async def close(self):
        await self._base.close()
```

### 第 2 步：更新 reasoning_effort 配置值

旧值 `"none"` 改为 `"disabled"`（自动兼容，会 warn）：

```yaml
# 迁移前
reasoning_effort: "none"    # 意图是关闭思考，但 DeepSeek 会忽略

# 迁移后
reasoning_effort: "disabled" # 明确关闭，DeepSeek → thinking.type=disabled
```

可选：添加启动校验，提前发现不兼容配置：

```python
from llm_compat import validate_config

for task in all_tasks:
    warnings = validate_config(task.model, task.reasoning_effort)
    for w in warnings:
        logger.warning(f"[{task.name}] {w}")
```

### 第 3 步：更新错误处理

```python
# 迁移前
try:
    result = await self._request_with_retry(...)
except httpx.HTTPStatusError as e:
    if e.response.status_code == 401:
        raise  # 不重试
    # ... 复杂的重试逻辑

# 迁移后
from llm_compat import FatalError, TimeoutError, JSONParseError

try:
    result = await client.chat(...)
except FatalError:
    # 401/403/404，不会重试，直接处理
    ...
except TimeoutError:
    # 超时，不会重试（同样输入同样超时）
    ...
except JSONParseError as e:
    # chat_json 解析失败，e.raw_content 有原始内容
    logger.error(f"JSON parse failed: {e.raw_content[:200]}")
```

### 第 4 步：利用 ChatResult 元数据（可选）

迁移前手动提取的信息现在自动可用：

```python
result = await client.chat("deepseek-v4-flash", messages, reasoning_effort="high")

# 之前需要自己解析 response JSON
# 现在直接用
print(result.content)       # 文本内容
print(result.usage)         # TokenUsage(prompt=10, completion=50, total=60)
print(result.latency_ms)    # 1500
print(result.request_id)    # a1b2c3d4 (自动生成，贯穿日志)
print(result.provider)      # "deepseek"
print(result.model)         # "deepseek-v4-flash"

# ChatResult.__str__() 返回 content，所以 f-string 直接用
logger.info(f"答案: {result}")
```

## 同步项目迁移

VTA 等同步项目用 `SyncLLMClient`，API 完全一样，只是没有 `await`：

```python
from llm_compat import SyncLLMClient

client = SyncLLMClient(base_url="...", api_key="...")
result = client.chat("deepseek-v4-flash", messages, reasoning_effort="high")
client.close()

# 或用 context manager
with SyncLLMClient(base_url="...", api_key="...") as client:
    result = client.chat(...)
```

## 自定义 Provider 映射

如果 New API 代理重命名了模型名：

```python
from llm_compat import register_provider, set_custom_patterns

# 单个注册
register_provider("my-proxy-ds-*", "deepseek")

# 批量注册（启动时从配置加载）
set_custom_patterns({
    "my-ds-*": "deepseek",
    "my-gpt-*": "openai_gpt4",
})
```

## 迁移检查清单

- [ ] 安装 llm-compat：`uv add git+https://github.com/zj1123581321/llm-compat.git`
- [ ] 创建薄封装 LLMClient（保留配置解析，删除 HTTP/重试/翻译）
- [ ] `reasoning_effort: "none"` → `"disabled"`
- [ ] 删除项目中的重试逻辑代码
- [ ] 删除项目中的 JSON code fence 清洗代码
- [ ] 删除项目中的 base64 图片编码辅助函数
- [ ] 更新错误处理为 FatalError/TimeoutError/JSONParseError
- [ ] 可选：添加 validate_config 启动校验
- [ ] 可选：使用 ChatResult.usage 替代手动 token 提取
- [ ] 运行测试验证
