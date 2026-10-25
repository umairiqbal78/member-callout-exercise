"""
AI Provider interfaces and exceptions for CrewLink.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class AIError(Exception):
    """Base exception for AI drafting errors."""
    pass


class AIConfigError(AIError):
    """Provider is not configured (e.g. missing API key). Returns 503 ai_not_configured."""
    pass


class AIUnavailableError(AIError):
    """
    Provider is unavailable (network error, timeout, 5xx, 429, or circuit breaker open).
    Returns 503 ai_unavailable.
    """
    pass


class AIRetryableError(AIUnavailableError):
    """Provider error that may be retried once (connect errors, read timeouts, 5xx)."""
    pass


class AINonRetryableError(AIUnavailableError):
    """Provider error that must NOT be retried (429, 4xx)."""
    pass


class AIInvalidOutputError(AIError):
    """
    Provider returned invalid output (malformed JSON, validation failure, push_preview > 120).
    Returns 502 ai_invalid_output.
    """
    pass


class AIProvider(ABC):
    """Abstract base class for AI draft providers."""

    @abstractmethod
    def generate(
        self,
        note: str,
        audience_classifications: Optional[List[str]] = None,
        timeout: float = 8.0,
    ) -> Dict[str, Any]:
        """
        Generate draft announcement from a raw note.

        Returns a dictionary with:
        {
            "title": str,
            "body": str,
            "push_preview": str,
            "warnings": List[str]
        }
        """
        pass
