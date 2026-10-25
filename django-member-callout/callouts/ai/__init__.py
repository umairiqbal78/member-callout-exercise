"""
AI drafting module for CrewLink.
"""

from .breaker import CircuitBreaker
from .fake import FakeProvider
from .gemini import GeminiProvider
from .interfaces import (
    AIConfigError,
    AIError,
    AIInvalidOutputError,
    AINonRetryableError,
    AIProvider,
    AIRetryableError,
    AIUnavailableError,
)
from .safety import deterministic_fact_check, validate_draft_output
from .service import draft_callout, get_ai_provider

__all__ = [
    "AIConfigError",
    "AIError",
    "AIInvalidOutputError",
    "AINonRetryableError",
    "AIProvider",
    "AIRetryableError",
    "AIUnavailableError",
    "CircuitBreaker",
    "FakeProvider",
    "GeminiProvider",
    "deterministic_fact_check",
    "draft_callout",
    "get_ai_provider",
    "validate_draft_output",
]
