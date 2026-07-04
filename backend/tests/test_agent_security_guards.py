from __future__ import annotations

import pytest

from productflow_backend.agent.guards import (
    GuardError,
    SessionRateLimit,
    TurnBudget,
    check_prompt_injection,
    rate_limit_session,
)


@pytest.mark.parametrize(
    "payload",
    [
        "rm -rf /",
        "DROP TABLE products",
        "DELETE FROM app_settings",
        "TRUNCATE gallery",
        "../../etc/passwd",
        "exec(malicious)",
        "os.system('echo pwned')",
    ],
)
def test_prompt_injection_guard_blocks_malicious_input(payload: str) -> None:
    with pytest.raises(GuardError):
        check_prompt_injection(payload)


def test_prompt_injection_guard_allows_benign_input() -> None:
    check_prompt_injection("Summer T-shirt", "casual", "19.99")


def test_prompt_injection_guard_ignores_non_strings() -> None:
    check_prompt_injection("safe", None, 42)  # type: ignore[arg-type]


def test_turn_budget_caps_tool_calls() -> None:
    budget = TurnBudget(max_tool_calls=4)
    for _ in range(4):
        budget.spend_tool_call()
    with pytest.raises(GuardError):
        budget.spend_tool_call()


def test_session_rate_limit_blocks_when_drained() -> None:
    bucket = SessionRateLimit(tokens=2, capacity=2, refill_per_sec=0.0)
    bucket.take()
    bucket.take()
    with pytest.raises(GuardError):
        bucket.take()


def test_rate_limit_session_falls_back_in_memory() -> None:
    session_id = "unit-test-session-drain"
    # Default capacity is 60 tokens/min; draining past it should raise.
    with pytest.raises(GuardError):
        for _ in range(120):
            rate_limit_session(session_id)