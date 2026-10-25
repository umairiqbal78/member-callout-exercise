"""
AI drafting orchestration service.
Enforces hard deadline budgets, retry policies, circuit breaking,
output validation, deterministic fact checking, and structured logging.
"""

import logging
import random
import time
from typing import Any, Dict, List, Optional, Tuple
from django.conf import settings
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

logger = logging.getLogger(__name__)


def get_ai_provider() -> Tuple[AIProvider, str]:
    """Return the configured AIProvider instance and its model identifier."""
    provider_name = getattr(settings, "AI_PROVIDER", "fake").lower()
    if provider_name == "gemini":
        model_name = getattr(settings, "GEMINI_MODEL", "gemini-2.5-flash")
        return GeminiProvider(), model_name
    return FakeProvider(), "fake"


def draft_callout(
    note: str,
    audience_classifications: Optional[List[str]] = None,
    user_id: Optional[str] = None,
    local_id: Optional[str] = None,
    custom_provider: Optional[AIProvider] = None,
    custom_model: Optional[str] = None,
) -> Tuple[Dict[str, Any], str]:
    """
    Orchestrate AI drafting of an announcement from a raw note.

    Returns:
        (validated_output, model_name)
    Raises:
        AIConfigError (503 ai_not_configured)
        AIUnavailableError (503 ai_unavailable)
        AIInvalidOutputError (502 ai_invalid_output)
        ValueError (if note exceeds 2,000 chars or is empty)
    """
    if not note or not note.strip():
        raise ValueError("Note cannot be empty")
    if len(note) > 2000:
        raise ValueError(f"Note exceeds 2,000 character limit ({len(note)} chars)")

    if custom_provider:
        provider = custom_provider
        model_name = custom_model or getattr(provider, "model", "custom")
    else:
        provider, model_name = get_ai_provider()

    breaker = CircuitBreaker()
    start_wall = time.time()
    start_mono = time.monotonic()
    total_budget = getattr(settings, "AI_TOTAL_BUDGET_SECONDS", 10.0)
    timeout_limit = getattr(settings, "AI_TIMEOUT_SECONDS", 8.0)
    deadline = start_mono + total_budget

    outcome = "error"
    is_probe = False

    try:
        # Check circuit breaker before attempting provider call
        is_probe = breaker.before_call()

        attempt = 1
        raw_output = None
        last_error = None

        while attempt <= 2:
            now_mono = time.monotonic()
            remaining_budget = deadline - now_mono
            if remaining_budget <= 0:
                raise AIUnavailableError("Deadline exceeded budget")

            call_timeout = min(timeout_limit, remaining_budget)
            try:
                raw_output = provider.generate(
                    note,
                    audience_classifications=audience_classifications,
                    timeout=call_timeout,
                )
                if time.monotonic() > deadline:
                    breaker.record_failure(is_probe)
                    raise AIUnavailableError("Deadline exceeded budget")
                # Successful call
                breaker.record_success(is_probe)
                break
            except AIRetryableError as e:
                last_error = e
                if attempt == 1:
                    # Retry ONCE with ~300ms jittered backoff only if budget remains
                    backoff = random.uniform(0.25, 0.35)
                    now_after_call = time.monotonic()
                    if (deadline - now_after_call) > backoff:
                        time.sleep(backoff)
                        attempt = 2
                        continue
                # Cannot retry or attempt 2 failed
                breaker.record_failure(is_probe)
                raise AIUnavailableError(str(e)) from e
            except AINonRetryableError as e:
                # 429 or 4xx: do not retry
                breaker.record_failure(is_probe)
                raise AIUnavailableError(str(e)) from e
            except AIConfigError:
                # Key not configured: does not count toward circuit breaker
                raise
            except AIInvalidOutputError:
                # Invalid output does not count toward breaker
                raise
            except Exception as e:
                breaker.record_failure(is_probe)
                raise AIUnavailableError(f"Unexpected provider error: {e}") from e

        if raw_output is None and last_error:
            raise AIUnavailableError(str(last_error))

        # Validate output schema & lengths (no silent truncation)
        validated = validate_draft_output(raw_output)

        # Deterministic fact checking
        validated["warnings"] = deterministic_fact_check(note, validated)

        outcome = "success"
        return validated, model_name

    except AIConfigError:
        outcome = "not_configured"
        raise
    except AIUnavailableError:
        outcome = "unavailable"
        raise
    except AIInvalidOutputError:
        outcome = "invalid_output"
        raise
    except ValueError:
        outcome = "bad_request"
        raise
    except Exception:
        outcome = "failed"
        raise
    finally:
        latency_ms = int((time.time() - start_wall) * 1000)
        # CRITICAL: Never log the note or the generated output
        logger.info(
            "ai_draft user=%s local=%s latency_ms=%d outcome=%s model=%s",
            user_id or "anonymous",
            local_id or "none",
            latency_ms,
            outcome,
            model_name,
        )
