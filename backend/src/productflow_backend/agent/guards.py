"""Guard rails for SeftFlow Copilot: prompt injection, tool quotas, rate limit."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

MAX_TOOL_CALLS_PER_TURN = 4
MAX_CONTEXT_TOKENS = 8000
RATE_LIMIT_TOKENS_PER_MIN = 60

_INJECTION_PATTERNS = [
    re.compile(r"(?i)\brm\s+-rf\b"),
    re.compile(r"(?i)\bDROP\s+TABLE\b"),
    re.compile(r"(?i)\bDELETE\s+FROM\b"),
    re.compile(r"(?i)\bTRUNCATE\b"),
    re.compile(r"\.\./"),
    re.compile(r"(?i)\bexec\s*\("),
    re.compile(r"(?i)os\.system"),
]


class GuardError(RuntimeError):
    """Raised when a guard denies a call."""


def check_prompt_injection(*values: str) -> None:
    """Reject obviously malicious tool arguments."""
    for value in values:
        if not isinstance(value, str):
            continue
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(value):
                raise GuardError(
                    f"Tool argument rejected by prompt-injection guard: {pattern.pattern}"
                )


@dataclass
class TurnBudget:
    """Per-turn quota tracker (in-memory)."""

    max_tool_calls: int = MAX_TOOL_CALLS_PER_TURN
    tool_calls_used: int = 0

    def spend_tool_call(self) -> None:
        if self.tool_calls_used >= self.max_tool_calls:
            raise GuardError(
                f"Tool-call quota exceeded ({self.max_tool_calls} per turn)"
            )
        self.tool_calls_used += 1


@dataclass
class SessionRateLimit:
    """Simple token-bucket rate limit for a single session (in-memory fallback)."""

    tokens: float = float(RATE_LIMIT_TOKENS_PER_MIN)
    capacity: float = float(RATE_LIMIT_TOKENS_PER_MIN)
    refill_per_sec: float = float(RATE_LIMIT_TOKENS_PER_MIN) / 60.0
    last_refill: float = field(default_factory=time.monotonic)

    def take(self, cost: float = 1.0) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)
        self.last_refill = now
        if self.tokens < cost:
            raise GuardError("Session rate limit exceeded, please slow down.")
        self.tokens -= cost


_SESSION_BUCKETS: dict[str, SessionRateLimit] = {}


def rate_limit_session(session_id: str, cost: float = 1.0) -> None:
    """In-process token-bucket keyed by session id."""
    bucket = _SESSION_BUCKETS.setdefault(session_id, SessionRateLimit())
    bucket.take(cost)


def redis_rate_limit(session_id: str, cost: float = 1.0) -> None:
    """Try Redis-backed token bucket; fall back to in-memory on any failure."""
    try:
        from productflow_backend.infrastructure.queue import get_broker

        client = get_broker().client
        key = f"agent:ratelimit:{session_id}"
        current = client.incr(key)
        if current == 1:
            client.expire(key, 60)
        if current > RATE_LIMIT_TOKENS_PER_MIN:
            raise GuardError("Session rate limit exceeded, please slow down.")
    except GuardError:
        raise
    except Exception:
        rate_limit_session(session_id, cost)
