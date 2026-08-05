Always respond in 中文

# llm-compat

轻量 Python 包，抹平 OpenAI 兼容 API 的多 provider 差异。专为 New API 代理 + 多模型切换场景设计。

## 项目背景

详见 `docs/design/business-context.md`。

核心场景：10+ Python 项目通过 New API（OpenAI 兼容代理）接入多家 LLM（DeepSeek V4、GPT、Gemini 等），需要统一处理 thinking/reasoning_effort 参数翻译、JSON 输出清洗、重试和日志。

## 文档结构

- `docs/design/` — 设计文档
  - `llm-compat-design.md` — 包结构和 API 设计
  - `business-context.md` — 业务背景和架构定位
  - `decision-log.md` — 关键决策记录
  - `content-fallback-design-context.md` — 内容审查降级设计
  - `sensitive-collector-design.md` — 敏感词积累系统设计
- `docs/guides/` — 用户指南
  - `integration-guide.md` — 从零接入指南（三步渐进式 + 已有项目迁移附录）
- `docs/research/` — API 调研资料

## 风险等级

**personal**（与 `.github/workflows/gate.yml` 的 `tier: personal` 保持一致，改一处必须改另一处）。

- P1 红线：数据丢失、**静默出错（结果错但不报错）**、崩溃。本库最典型的 P1 形态是静默出错——
  provider 参数翻译错了但 API 不报错（例：issue #7，thinking 没关成还报告已关闭），
  下游 10+ 项目完全不可见。
- review 轮次上限 3，收敛条件「连续 1 轮无新增 P1」。
- **例外**：改动核心落在失败路径（fallback 链、retry、错误分类）或 provider 能力表/翻译层时，
  按 core-lead 的 infra 例外走上一档收敛，即「连续 2 轮无新增 P1」。这类改动的缺陷
  正好都是静默型的。

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
