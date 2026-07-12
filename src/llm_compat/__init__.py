"""llm-compat: 轻量 Python 包，抹平 OpenAI 兼容 API 的多 provider 差异。"""
from llm_compat._compat import (
    normalize_reasoning_effort,
    validate_config,
    validate_fallback_config,
)
from llm_compat._trace import CallTrace, ModelAttempt, RouteDecision
from llm_compat._types import ChatResult, LLMStats, TokenUsage
from llm_compat.client import LLMClient
from llm_compat.errors import (
    ContentPolicyError,
    FatalError,
    JSONParseError,
    LLMCallError,
    LLMError,
    RetryableError,
    SkipRequestError,
    TimeoutError,
    TruncationError,
)
from llm_compat.json_utils import parse_json, parse_json_model, pydantic_to_json_schema
from llm_compat.providers import (
    build_request_payload,
    describe_from_payload,
    detect_provider,
    register_provider,
    set_custom_patterns,
)
from llm_compat.refusal import RefusalContext, RefusalDetector
from llm_compat.sync import SyncLLMClient

__all__ = [
    "LLMClient",
    "SyncLLMClient",
    "ChatResult",
    "TokenUsage",
    "LLMStats",
    "LLMError",
    "LLMCallError",
    "RetryableError",
    "FatalError",
    "TimeoutError",
    "TruncationError",
    "JSONParseError",
    "SkipRequestError",
    "ContentPolicyError",
    "CallTrace",
    "RouteDecision",
    "ModelAttempt",
    "RefusalContext",
    "RefusalDetector",
    "validate_fallback_config",
    "detect_provider",
    "build_request_payload",
    "describe_from_payload",
    "register_provider",
    "set_custom_patterns",
    "parse_json",
    "parse_json_model",
    "pydantic_to_json_schema",
    "validate_config",
    "normalize_reasoning_effort",
]
