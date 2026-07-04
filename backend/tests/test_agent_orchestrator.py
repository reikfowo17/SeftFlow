from __future__ import annotations

import asyncio
from pathlib import Path

from productflow_backend.agent.orchestrator import AgentTurnEvent, run_agent_turn


def _collect(session_id: str, message: str) -> list[AgentTurnEvent]:
    async def _run() -> list[AgentTurnEvent]:
        return [event async for event in run_agent_turn(session_id=session_id, message=message)]

    return asyncio.run(_run())


def _kinds(events: list[AgentTurnEvent]) -> list[str]:
    return [event.kind for event in events]


def test_fallback_lists_products(configured_env: Path) -> None:
    events = _collect("orch-list", "please list products")
    tool_calls = [e for e in events if e.kind == "tool_call"]
    assert tool_calls and tool_calls[0].payload["name"] == "list_products"
    assert events[-1].kind == "done"


def test_fallback_creates_product(configured_env: Path) -> None:
    events = _collect("orch-create", "create product Aurora Lamp")
    tool_calls = [e for e in events if e.kind == "tool_call"]
    assert tool_calls and tool_calls[0].payload["name"] == "create_product"
    results = [e for e in events if e.kind == "tool_result"]
    assert results and results[0].payload["result"]["created"] is True


def test_fallback_default_help_text(configured_env: Path) -> None:
    events = _collect("orch-help", "hello there")
    assert _kinds(events) == ["text", "done"]
    assert "fallback" in events[0].payload["text"].lower()


def test_prompt_injection_short_circuits_turn(configured_env: Path) -> None:
    events = _collect("orch-evil", "please DROP TABLE products now")
    assert _kinds(events) == ["error", "done"]


def test_agent_turn_event_sse_serialization() -> None:
    event = AgentTurnEvent("text", {"text": "hi"})
    line = event.to_sse()
    assert line.startswith("data: ")
    assert line.endswith("\n\n")