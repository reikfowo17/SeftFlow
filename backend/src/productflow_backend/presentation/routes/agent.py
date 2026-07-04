"""FastAPI route for the SeftFlow Copilot agent (SSE streaming)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from productflow_backend.agent import run_agent_turn
from productflow_backend.presentation.deps import require_admin

router = APIRouter(prefix="/api/agent", tags=["agent"], dependencies=[Depends(require_admin)])


class AgentChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None


@router.post("/chat")
async def agent_chat(request: Request, payload: AgentChatRequest) -> StreamingResponse:
    """SSE endpoint that streams the agent turn events."""
    session_id = payload.session_id or request.cookies.get("agent_session") or uuid4().hex

    async def event_stream() -> AsyncIterator[str]:
        async for event in run_agent_turn(session_id=session_id, message=payload.message):
            yield event.to_sse()

    response = StreamingResponse(event_stream(), media_type="text/event-stream")
    response.set_cookie("agent_session", session_id, httponly=True, samesite="lax")
    return response


@router.get("/sessions/{session_id}")
async def agent_session_status(session_id: str) -> dict:
    """Placeholder: sessions are held in-memory by ADK InMemorySessionService."""
    return {"session_id": session_id, "note": "In-memory session; history not persisted in v1."}
