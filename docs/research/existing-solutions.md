# 现有方案调研

调研时间：2026-05-07

## 评估的方案

### 1. LiteLLM (⭐45,960)

- **仓库**: https://github.com/BerriAI/litellm
- **定位**: 完整 AI Gateway + Python SDK，支持 100+ LLM API
- **包大小**: 14.8 MB（PyPI 源码包）
- **依赖**: 重量级，几十个依赖

**不适合的原因**：
- 我们已有 New API 作为代理网关，LiteLLM 的核心价值（路由、鉴权、负载均衡）完全重叠
- 作为 SDK 使用太重，引入大量不需要的依赖
- 对于 "单 base_url + 按模型名翻译参数" 的场景过于复杂

### 2. aisuite (Andrew Ng)

- **仓库**: https://github.com/andrewyng/aisuite
- **定位**: 轻量统一接口，抽象多 provider SDK 差异
- **特点**: 按 provider 可选装对应 SDK

**不适合的原因**：
- 设计思路是直连各家 API（每家装对应 SDK），而我们统一走 New API 一个入口
- 架构不匹配：我们不需要管理多个 provider 的 SDK 和 API key

### 3. 其他小项目

| 项目 | Stars | 评估 |
|------|-------|------|
| Nayjest/lm-proxy | 116 | HTTP 代理，与 New API 重叠 |
| virtusoul-router | 13 | ML 路由，不是我们的需求 |
| LLM-API-Key-Proxy | 475 | 又一个代理，重叠 |

## 结论

没有现成方案适合我们的场景（New API 代理 + 客户端侧 provider 差异抹平）。

**市场空白**：现有工具都在做 "代理/网关" 层面的统一，没有轻量的 "客户端 payload 翻译" 库。我们的需求是一个 thin wrapper over httpx，只做参数翻译，不做路由。

## 决定

自建 `llm-compat`，核心约 300 行代码，零重依赖（仅 httpx），专注于：
1. Provider 检测（模型名 → 厂商族）
2. thinking/reasoning_effort 参数翻译
3. JSON 响应清洗
4. 重试 + 可观测性
