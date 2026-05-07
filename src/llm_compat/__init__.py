"""llm-compat: 轻量 Python 包，抹平 OpenAI 兼容 API 的多 provider 差异。"""
from llm_compat._compat import normalize_reasoning_effort, validate_config
from llm_compat._types import ChatResult, LLMStats, TokenUsage
from llm_compat.client import LLMClient
from llm_compat.errors import (
    FatalError,
    JSONParseError,
    LLMError,
    RetryableError,
    TimeoutError,
    TruncationError,
)
from llm_compat.json_utils import parse_json, parse_json_model
from llm_compat.providers import (
    build_request_payload,
    describe_from_payload,
    detect_provider,
    register_provider,
    set_custom_patterns,
)
from llm_compat.sync import SyncLLMClient

__all__ = [
    "LLMClient",
    "SyncLLMClient",
    "ChatResult",
    "TokenUsage",
    "LLMStats",
    "LLMError",
    "RetryableError",
    "FatalError",
    "TimeoutError",
    "TruncationError",
    "JSONParseError",
    "detect_provider",
    "build_request_payload",
    "describe_from_payload",
    "register_provider",
    "set_custom_patterns",
    "parse_json",
    "parse_json_model",
    "validate_config",
    "normalize_reasoning_effort",
]
