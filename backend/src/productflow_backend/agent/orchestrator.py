"""SeftFlow Copilot orchestrator (Google ADK root LlmAgent).

Exposes `build_orchestrator()` to construct the agent tree and `run_agent_turn()`
as the public entrypoint used by the FastAPI SSE route.

ADK is imported lazily so the rest of the app remains importable without the
google-adk dependency installed.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from productflow_backend.agent import prompts, subagents
from productflow_backend.agent.guards import (
    GuardError,
    TurnBudget,
    check_prompt_injection,
    redis_rate_limit,
)
from productflow_backend.agent.tools import SeftFlowTools, build_tool_map


@dataclass
class AgentTurnEvent:
    """A single streamed event surfaced by the orchestrator."""

    kind: str  # "text" | "tool_call" | "tool_result" | "error" | "done"
    payload: dict[str, Any]

    def to_sse(self) -> str:
        return f"data: {json.dumps({'kind': self.kind, 'payload': self.payload})}\n\n"


def build_orchestrator() -> Any:
    """Return an ADK root LlmAgent wired with sub-agents.

    Returns None when ADK is not installed or no Gemini/Google API key is
    configured, so callers fall back to the deterministic keyword router
    instead of issuing a live LLM call that would fail without credentials.
    """
    import os

    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        return None
    try:
        from google.adk.agents import LlmAgent  # type: ignore
    except Exception:
        return None
    return LlmAgent(
        name="SeftFlowCopilot",
        model="gemini-2.5-flash",
        description="Root SeftFlow copilot that routes to sub-agents.",
        instruction=prompts.ORCHESTRATOR_PROMPT,
        sub_agents=[
            agent
            for agent in (subagents.build_copywriter_agent(), subagents.build_art_director_agent())
            if agent is not None
        ],
    )


async def run_agent_turn(
    session_id: str,
    message: str,
) -> AsyncIterator[AgentTurnEvent]:
    """Yield streaming events for a single user turn.

    When Google ADK is available we route the message through the orchestrator
    tree. Otherwise we run a deterministic fallback that inspects the message,
    picks a plausible tool from the schema, and returns a trace-only response
    (useful for local tests, MCP smoke checks, and CI).
    """
    try:
        check_prompt_injection(message)
        redis_rate_limit(session_id)
    except GuardError as exc:
        yield AgentTurnEvent("error", {"message": str(exc)})
        yield AgentTurnEvent("done", {})
        return

    budget = TurnBudget()
    tools = SeftFlowTools(session_id=session_id, budget=budget)
    tool_map = build_tool_map(tools)

    orchestrator = build_orchestrator()
    if orchestrator is None:
        async for event in _fallback_turn(message, tool_map):
            yield event
        return

    async for event in _adk_turn(orchestrator, session_id, message, tool_map):
        yield event


async def _fallback_turn(
    message: str,
    tool_map: dict[str, Any],
) -> AsyncIterator[AgentTurnEvent]:
    """Deterministic fallback that avoids external LLM calls.

    Chooses a tool by simple keyword heuristics; used when ADK is missing.
    """
    lowered = message.lower()
    chosen: str | None = None
    args: dict[str, Any] = {}
    if "list" in lowered and "product" in lowered:
        chosen = "list_products"
    elif "create product" in lowered or "new product" in lowered:
        chosen = "create_product"
        args = {"name": message[:80]}
    elif "image" in lowered or "poster" in lowered or "render" in lowered:
        chosen = "generate_image"
        args = {"prompt": message}
    elif "copy" in lowered:
        yield AgentTurnEvent(
            "text",
            {"text": "Copy generation requires a product id. Try `list_products` first."},
        )
        yield AgentTurnEvent("done", {})
        return
    else:
        yield AgentTurnEvent(
            "text",
            {
                "text": (
                    "SeftFlow Copilot (fallback mode). Try: 'list products', "
                    "'create product ...', or 'generate an image of ...'."
                )
            },
        )
        yield AgentTurnEvent("done", {})
        return

    yield AgentTurnEvent("tool_call", {"name": chosen, "arguments": args})
    try:
        result = await asyncio.to_thread(tool_map[chosen], **args)
    except Exception as exc:
        yield AgentTurnEvent("error", {"message": str(exc)})
        yield AgentTurnEvent("done", {})
        return
    yield AgentTurnEvent("tool_result", {"name": chosen, "result": result})
    yield AgentTurnEvent(
        "text",
        {"text": f"Executed `{chosen}`. Inspect the tool_result payload for details."},
    )
    yield AgentTurnEvent("done", {})


async def _adk_turn(
    orchestrator: Any,
    session_id: str,
    message: str,
    tool_map: dict[str, Any],
) -> AsyncIterator[AgentTurnEvent]:
    """Route the message through the ADK Runner.

    We bind function tools by wrapping tool_map entries as ADK `FunctionTool`s.
    Streaming events are translated into `AgentTurnEvent`s for SSE.
    """
    try:
        from google.adk.runners import Runner  # type: ignore
        from google.adk.sessions import InMemorySessionService  # type: ignore
        from google.adk.tools import FunctionTool  # type: ignore
        from google.genai import types  # type: ignore
    except Exception as exc:
        yield AgentTurnEvent("error", {"message": f"ADK import failed: {exc}"})
        yield AgentTurnEvent("done", {})
        return

    orchestrator.tools = [FunctionTool(func=fn) for fn in tool_map.values()]

    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="productflow", user_id=session_id, session_id=session_id
    )
    runner = Runner(
        app_name="productflow",
        agent=orchestrator,
        session_service=session_service,
    )
    content = types.Content(role="user", parts=[types.Part.from_text(text=message)])
    try:
        async for event in runner.run_async(
            user_id=session_id, session_id=session.id, new_message=content
        ):
            if event.is_final_response() and event.content and event.content.parts:
                text_parts = [p.text for p in event.content.parts if getattr(p, "text", None)]
                if text_parts:
                    yield AgentTurnEvent("text", {"text": "".join(text_parts)})
            elif event.get_function_calls():
                for call in event.get_function_calls():
                    yield AgentTurnEvent(
                        "tool_call",
                        {"name": call.name, "arguments": dict(call.args or {})},
                    )
            elif event.get_function_responses():
                for response in event.get_function_responses():
                    yield AgentTurnEvent(
                        "tool_result",
                        {"name": response.name, "result": response.response},
                    )
    except Exception as exc:
        yield AgentTurnEvent("error", {"message": f"Agent turn failed: {exc}"})
    yield AgentTurnEvent("done", {})
