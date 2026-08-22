"""端到端复现 issue #22 生产场景：模拟 VideoTranscriptAPI 的真实调用形态。"""
from __future__ import annotations

from pytest_httpx import HTTPXMock

from llm_compat import LLMClient

# issue #22 原始场景：B站《医疗事故罪的真相：对话医疗纠纷律师》20853 字校对稿走 summary prompt
CHAIN = {"deepseek-v4-pro": ["deepseek-v4-flash", "gpt-4.1-mini", "gemini-2.5-flash"]}
MESSAGES = [{"role": "user", "content": "请总结以下访谈内容：" + "访谈正文。" * 3000}]


def _summary_body() -> str:
    """13402 字的完整正常总结，正文含 4 次「违反」、1 次「无法提供」——issue 里的真实形态。"""
    body = "本期访谈围绕医疗事故罪展开。" * 1000
    body = body[:7900] + "严重违反诊疗技术规范或常规：严重程度需由鉴定部门评判。" + body[7900:]
    body = body[:9000] + "医院无法提供完整病历时，举证责任发生转移。" + body[9000:]
    body += "违反注意义务、违反告知义务、违反规范三者需分别认定。"
    assert len(body) >= 13000, len(body)
    assert body.count("违反") >= 4
    return body


def _resp(
    content: str, finish_reason: str = "stop", model: str = "deepseek-v4-pro"
) -> dict:
    return {
        "id": "chatcmpl-e2e", "object": "chat.completion", "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 20853, "completion_tokens": 9294, "total_tokens": 30147},
    }


async def test_issue22_long_legal_summary_succeeds_without_fallback(httpx_mock: HTTPXMock):
    """issue #22 主场景：13402 字合规题材总结必须一次成功，不烧 fallback 链。"""
    summary = _summary_body()
    httpx_mock.add_response(json=_resp(summary))
    async with LLMClient(base_url="https://newapi.test/v1", api_key="sk-t",
                         content_fallbacks=CHAIN) as client:
        result = await client.chat("deepseek-v4-pro", MESSAGES)
        assert result.content == summary
        assert result.fallback_from is None, "不该发生任何 fallback"
        assert result.fallback_chain == []
        assert result.refusal_suspected is False
        assert result.trace.final_outcome == "success"
        assert len(result.trace.model_attempts) == 1, "只该发一次请求"
        assert client.stats.success_count == 1
        assert client.stats.fallback_count == 0
    assert len(httpx_mock.get_requests()) == 1


async def test_real_refusal_still_falls_back(httpx_mock: HTTPXMock):
    """对照组：真拒绝仍要降级到海外模型并成功。"""
    httpx_mock.add_response(json=_resp("抱歉，我无法回答涉及该内容的问题。"))
    httpx_mock.add_response(json=_resp("这是海外模型给出的完整总结。", model="gemini-2.5-flash"))
    async with LLMClient(base_url="https://newapi.test/v1", api_key="sk-t",
                         content_fallbacks=CHAIN) as client:
        result = await client.chat("deepseek-v4-pro", MESSAGES)
        assert result.content == "这是海外模型给出的完整总结。"
        assert result.fallback_from == "deepseek-v4-pro"
        assert client.stats.fallback_count == 1


async def test_chain_exhausted_returns_best_not_failure(httpx_mock: HTTPXMock):
    """issue #22 的失败形态：全链被判拒绝时不再抛 All models refused。"""
    for i, model in enumerate(
        ["deepseek-v4-pro", "deepseek-v4-flash", "gpt-4.1-mini", "gemini-2.5-flash"]
    ):
        httpx_mock.add_response(json=_resp("抱歉，我无法回答。" + "补充说明。" * i, model=model))
    async with LLMClient(base_url="https://newapi.test/v1", api_key="sk-t",
                         content_fallbacks=CHAIN) as client:
        result = await client.chat("deepseek-v4-pro", MESSAGES)
        assert result.refusal_suspected is True
        assert result.refusal_evidence.layer == "text_pattern"
        assert result.trace.final_outcome == "content_policy_recovered"
        assert "补充说明。补充说明。补充说明。" in result.content, "应返回最长候选"


async def test_downstream_escape_hatch_disables_text_layer(httpx_mock: HTTPXMock):
    """下游逃生门：关掉文本层后，同样的拒绝语不再触发 fallback。"""
    httpx_mock.add_response(json=_resp("抱歉，我无法回答涉及该内容的问题。"))
    async with LLMClient(base_url="https://newapi.test/v1", api_key="sk-t",
                         content_fallbacks=CHAIN, refusal_max_content_length=0) as client:
        result = await client.chat("deepseek-v4-pro", MESSAGES)
        assert result.fallback_from is None
        assert client.stats.fallback_count == 0
    assert len(httpx_mock.get_requests()) == 1
