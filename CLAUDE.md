Always respond in 中文

# llm-compat

轻量 Python 包，抹平 OpenAI 兼容 API 的多 provider 差异。专为 New API 代理 + 多模型切换场景设计。

## 项目背景

详见 `docs/business-context.md`。

核心场景：10+ Python 项目通过 New API（OpenAI 兼容代理）接入多家 LLM（DeepSeek V4、GPT、Gemini 等），需要统一处理 thinking/reasoning_effort 参数翻译、JSON 输出清洗、重试和日志。

## 设计文档

- `docs/llm-compat-design.md` — 包结构和 API 设计
- `docs/business-context.md` — 业务背景和架构定位
- `docs/decision-log.md` — 关键决策记录
- `docs/research/` — API 调研资料

## 设计约束

- 零重依赖（核心只依赖 httpx，Pydantic 可选）
- 不做路由/鉴权（New API 已做）
- 不做配置管理和 Prompt 模板（各项目自定义）
- 异步优先（httpx.AsyncClient）
- 日志用标准 logging（不强制 loguru）
- 私有 GitHub 包，不发 PyPI

## 参考实现

- 已有 provider 翻译层实现：`/home/zlx/projects/personal/VideoTranscriptAPI/src/video_transcript_api/llm/providers.py`
- 已有工程指南：`/home/zlx/projects/personal/VideoTranscriptAPI/docs/development/llm/engineering_guide.md`
- 第一个消费者：`/home/zlx/projects/personal/Memos_auto_with_AI/backend/core/clients/llm_client.py`

## 代码规范

- Python 3.11+
- 包管理器使用 uv
- 类型注解完备，支持 mypy 严格模式
- 测试使用 pytest
- 代码检查使用 ruff
