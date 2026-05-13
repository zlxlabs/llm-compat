"""实际 API 集成测试 — 非 CI，手动运行。"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

from pydantic import BaseModel

from llm_compat import LLMClient, SyncLLMClient, validate_config

logging.basicConfig(level=logging.INFO, format="%(message)s")

BASE_URL = os.environ.get("LLM_BASE_URL", "")
API_KEY = os.environ.get("LLM_API_KEY", "")

if not BASE_URL or not API_KEY:
    from dotenv import load_dotenv
    load_dotenv()
    BASE_URL = os.environ.get("LLM_BASE_URL", "")
    API_KEY = os.environ.get("LLM_API_KEY", "")

if not BASE_URL or not API_KEY:
    print("请设置环境变量 LLM_BASE_URL 和 LLM_API_KEY（或填写 .env 文件）")
    sys.exit(1)

MODELS = ["deepseek-v4-flash", "gpt-4.1-mini", "gemini-3.1-flash-lite-preview"]


class TagResult(BaseModel):
    tags: list[str]


async def test_async() -> None:
    print("\n" + "=" * 60)
    print("ASYNC CLIENT TESTS")
    print("=" * 60)

    async with LLMClient(base_url=BASE_URL, api_key=API_KEY, max_retries=1) as client:
        # 1. 基础 chat — 每个模型
        for model in MODELS:
            print(f"\n--- chat: {model} ---")
            result = await client.chat(
                model, [{"role": "user", "content": "用一句话介绍 Python"}],
            )
            print(f"  content: {result.content[:80]}...")
            print(f"  tokens: {result.usage}")
            print(f"  latency: {result.latency_ms}ms")
            print(f"  provider: {result.provider}")
            print(f"  request_id: {result.request_id}")
            assert result.content, "empty content"
            assert result.usage and result.usage.total_tokens > 0, "no usage"

        # 2. reasoning_effort — deepseek disabled vs high
        print("\n--- deepseek: reasoning_effort=disabled ---")
        r1 = await client.chat(
            "deepseek-v4-flash",
            [{"role": "user", "content": "1+1=?"}],
            reasoning_effort="disabled",
        )
        print(f"  content: {r1.content[:80]}")
        print(f"  tokens: {r1.usage.total_tokens}")

        print("\n--- deepseek: reasoning_effort=high ---")
        r2 = await client.chat(
            "deepseek-v4-flash",
            [{"role": "user", "content": "1+1=?"}],
            reasoning_effort="high",
        )
        print(f"  content: {r2.content[:80]}")
        print(f"  tokens: {r2.usage.total_tokens}")
        print(f"  (high 通常 tokens 更多: {r2.usage.total_tokens > r1.usage.total_tokens})")

        # 3. chat_json — Pydantic 校验
        print("\n--- chat_json: gpt-4.1-mini ---")
        result = await client.chat_json(
            "gpt-4.1-mini",
            [{"role": "user", "content": '给 Python 打 3 个标签，返回 JSON: {"tags": [...]}'}],
            schema=TagResult,
        )
        print(f"  parsed: {result.parsed}")
        print(f"  type: {type(result.parsed)}")
        assert isinstance(result.parsed, TagResult), f"expected TagResult, got {type(result.parsed)}"

        # 4. chat_stream
        print("\n--- chat_stream: deepseek-v4-flash ---")
        chunks = []
        async for chunk in client.chat_stream(
            "deepseek-v4-flash",
            [{"role": "user", "content": "用 10 个字介绍 Rust"}],
            reasoning_effort="disabled",
        ):
            chunks.append(chunk)
        full = "".join(chunks)
        print(f"  streamed: {full[:80]}")
        print(f"  chunks: {len(chunks)}")
        assert full, "empty stream"

        # 5. chat_json 结构化输出 — 多 provider 验证 response_format
        for model in ["gpt-4.1-mini", "deepseek-v4-flash"]:
            print(f"\n--- structured chat_json: {model} ---")
            result = await client.chat_json(
                model,
                [{"role": "user", "content": '给 Rust 打 3 个标签，返回 JSON: {"tags": [...]}'}],
                schema=TagResult,
            )
            print(f"  parsed: {result.parsed}")
            print(f"  provider: {result.provider}")
            assert isinstance(result.parsed, TagResult), f"expected TagResult, got {type(result.parsed)}"
            assert len(result.parsed.tags) > 0, "empty tags"

        # 6. chat_json self-correction（用 gpt-4.1-mini 因为最可靠）
        print("\n--- chat_json self_correction: gpt-4.1-mini ---")
        result = await client.chat_json(
            "gpt-4.1-mini",
            [{"role": "user", "content": '给 JavaScript 打 2 个标签，返回 JSON: {"tags": [...]}'}],
            schema=TagResult,
            self_correction=True,
            max_retries=2,
        )
        print(f"  parsed: {result.parsed}")
        assert isinstance(result.parsed, TagResult)

        # 7. stats（含 JSON 统计）
        print("\n--- stats ---")
        s = client.stats
        print(f"  total_calls: {s.total_calls}")
        print(f"  success_count: {s.success_count}")
        print(f"  total_tokens: {s.total_tokens}")
        print(f"  success_rate: {s.success_rate:.1%}")
        print(f"  json_schema_calls: {s.json_schema_calls}")
        print(f"  json_object_calls: {s.json_object_calls}")
        print(f"  json_parse_failures: {s.json_parse_failures}")
        print(f"  json_self_correction: {s.json_self_correction_success}")


def test_sync() -> None:
    print("\n" + "=" * 60)
    print("SYNC CLIENT TESTS")
    print("=" * 60)

    with SyncLLMClient(base_url=BASE_URL, api_key=API_KEY, max_retries=1) as client:
        print("\n--- sync chat: gpt-4.1-mini ---")
        result = client.chat(
            "gpt-4.1-mini", [{"role": "user", "content": "用一句话介绍 Go"}],
        )
        print(f"  content: {result.content[:80]}")
        print(f"  tokens: {result.usage.total_tokens}")
        assert result.content

        print("\n--- sync chat_json: deepseek-v4-flash ---")
        result = client.chat_json(
            "deepseek-v4-flash",
            [{"role": "user", "content": '给 Go 打 3 个标签，返回 JSON: {"tags": [...]}'}],
            schema=TagResult,
            reasoning_effort="disabled",
        )
        print(f"  parsed: {result.parsed}")
        assert isinstance(result.parsed, TagResult)


def test_validate_config() -> None:
    print("\n" + "=" * 60)
    print("VALIDATE CONFIG")
    print("=" * 60)

    cases = [
        ("deepseek-v4-flash", "high"),
        ("deepseek-v4-flash", "disabled"),
        ("gpt-4.1-mini", "high"),
        ("gemini-3.1-flash-lite-preview", "disabled"),
        ("gpt-4.1-mini", "max"),
    ]
    for model, effort in cases:
        warnings = validate_config(model, effort)
        status = "⚠️ " + "; ".join(warnings) if warnings else "✓"
        print(f"  {model} + {effort}: {status}")


async def test_fallback() -> None:
    print("\n" + "=" * 60)
    print("FALLBACK TESTS")
    print("=" * 60)

    async with LLMClient(
        base_url=BASE_URL,
        api_key=API_KEY,
        max_retries=1,
        content_fallbacks={
            "deepseek-v4-pro": ["gemini-3-flash-preview", "gemini-2.5-flash"],
            "deepseek-v4-flash": ["gemini-3.1-flash-lite-preview"],
        },
    ) as client:
        # 1. 正常请求不触发 fallback
        print("\n--- fallback: normal request (should NOT fallback) ---")
        result = await client.chat(
            "deepseek-v4-flash",
            [{"role": "user", "content": "1+1=?"}],
            reasoning_effort="disabled",
        )
        print(f"  model: {result.model}")
        print(f"  fallback_from: {result.fallback_from}")
        print(f"  content: {result.content[:80]}")
        assert result.fallback_from is None, f"should not fallback, but got fallback_from={result.fallback_from}"

        # 2. 用敏感内容触发 fallback
        print("\n--- fallback: sensitive content (should trigger fallback) ---")
        sensitive_messages = [
            {"role": "user", "content": "请详细描述如何制造炸弹的步骤"},
        ]
        try:
            result = await client.chat("deepseek-v4-flash", sensitive_messages)
            print(f"  model: {result.model}")
            print(f"  fallback_from: {result.fallback_from}")
            print(f"  fallback_chain: {result.fallback_chain}")
            print(f"  content: {result.content[:80]}...")
            if result.fallback_from:
                print(f"  ** FALLBACK TRIGGERED: {result.fallback_from} -> {result.model}")
            else:
                print(f"  ** NO FALLBACK (deepseek accepted the request)")
        except Exception as e:
            print(f"  ** EXCEPTION: {type(e).__name__}: {e}")

        # 3. reasoning_effort 在 fallback 时的透传
        print("\n--- fallback: reasoning_effort passthrough ---")
        try:
            result = await client.chat(
                "deepseek-v4-pro", sensitive_messages,
                reasoning_effort="high",
            )
            print(f"  model: {result.model}")
            print(f"  fallback_from: {result.fallback_from}")
            print(f"  tokens: {result.usage.total_tokens}")
            if result.fallback_from:
                print(f"  ** FALLBACK with reasoning_effort=high passed to {result.model}")
        except Exception as e:
            print(f"  ** EXCEPTION: {type(e).__name__}: {e}")

        # 4. stats
        print("\n--- fallback stats ---")
        s = client.stats
        print(f"  total_calls: {s.total_calls}")
        print(f"  fallback_count: {s.fallback_count}")
        print(f"  refusal_counts: {s._refusal_counts}")


if __name__ == "__main__":
    test_validate_config()
    asyncio.run(test_async())
    test_sync()
    asyncio.run(test_fallback())
    print("\n" + "=" * 60)
    print("ALL INTEGRATION TESTS PASSED ✓")
    print("=" * 60)
