"""
Redis-backed circuit breaker for AI drafting provider calls.
"""

import time
from typing import List, Tuple
from django.conf import settings
from django.core.cache import cache
from .interfaces import AIUnavailableError

FAILURES_KEY = "ai:breaker:failures"
OPEN_UNTIL_KEY = "ai:breaker:open_until"
PROBE_LOCK_KEY = "ai:breaker:probe_lock"


class CircuitBreaker:
    """
    Tracks provider failures across worker processes via shared Redis cache.
    - If failures in rolling window >= AI_BREAKER_FAILURES: opens for AI_BREAKER_COOLDOWN_SECONDS.
    - During cooldown, immediately raises AIUnavailableError without contacting the provider.
    - After cooldown, allows exactly ONE probe request via cache.add lock.
    """

    def __init__(
        self,
        failures_threshold: int | None = None,
        window_seconds: int | None = None,
        cooldown_seconds: int | None = None,
    ):
        self.failures_threshold = failures_threshold or getattr(settings, "AI_BREAKER_FAILURES", 5)
        self.window_seconds = window_seconds or getattr(settings, "AI_BREAKER_WINDOW_SECONDS", 60)
        self.cooldown_seconds = cooldown_seconds or getattr(settings, "AI_BREAKER_COOLDOWN_SECONDS", 60)

    def before_call(self) -> bool:
        """
        Check breaker status.
        Returns is_probe (True if this call is the single probe allowed after cooldown, False otherwise).
        Raises AIUnavailableError if breaker is open or another probe is in-flight.
        """
        open_until = cache.get(OPEN_UNTIL_KEY)
        if open_until is None:
            return False

        now = time.time()
        if now < open_until:
            raise AIUnavailableError("Write the callout by hand or try again shortly.")

        # Cooldown expired: breaker is half-open. Exactly one probe allowed.
        lock_ttl = int(getattr(settings, "AI_TOTAL_BUDGET_SECONDS", 10.0) + 5)
        is_probe = cache.add(PROBE_LOCK_KEY, "1", timeout=lock_ttl)
        if not is_probe:
            # Another probe is currently in flight
            raise AIUnavailableError("Write the callout by hand or try again shortly.")

        return True

    def record_success(self, is_probe: bool = False):
        """Called when a provider request succeeds."""
        if is_probe or cache.get(OPEN_UNTIL_KEY):
            # Probe succeeded: close the breaker completely
            cache.delete(OPEN_UNTIL_KEY)
            cache.delete(FAILURES_KEY)
            cache.delete(PROBE_LOCK_KEY)

    def record_failure(self, is_probe: bool = False):
        """Called when a provider request fails with a network error, timeout, 5xx, or 429."""
        now = time.time()
        if is_probe:
            # Probe failed: re-open breaker for another cooldown period
            cache.set(OPEN_UNTIL_KEY, now + self.cooldown_seconds, timeout=self.cooldown_seconds + 300)
            cache.delete(PROBE_LOCK_KEY)
            return

        # Regular failure: track rolling window
        raw_failures = cache.get(FAILURES_KEY) or []
        cutoff = now - self.window_seconds
        failures = [t for t in raw_failures if t >= cutoff]
        failures.append(now)

        if len(failures) >= self.failures_threshold:
            # Threshold reached: trip circuit breaker
            cache.set(OPEN_UNTIL_KEY, now + self.cooldown_seconds, timeout=self.cooldown_seconds + 300)
            cache.delete(FAILURES_KEY)
        else:
            cache.set(FAILURES_KEY, failures, timeout=self.window_seconds + 10)

    @classmethod
    def reset(cls):
        """Reset breaker state (useful in tests)."""
        cache.delete(OPEN_UNTIL_KEY)
        cache.delete(FAILURES_KEY)
        cache.delete(PROBE_LOCK_KEY)
