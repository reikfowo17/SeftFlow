"""SeftFlow Copilot sub-agents (Google ADK LlmAgent instances)."""

from __future__ import annotations

from typing import Any

from productflow_backend.agent import prompts


def _try_import_adk() -> Any:
    try:
        from google.adk.agents import LlmAgent  # type: ignore

        return LlmAgent
    except Exception:
        return None


def build_copywriter_agent() -> Any:
    LlmAgent = _try_import_adk()
    if LlmAgent is None:
        return None
    return LlmAgent(
        name="CopywriterAgent",
        model="gemini-2.5-flash",
        description="Writes and refines short-form product copy.",
        instruction=prompts.COPYWRITER_PROMPT,
    )


def build_art_director_agent() -> Any:
    LlmAgent = _try_import_adk()
    if LlmAgent is None:
        return None
    return LlmAgent(
        name="ArtDirectorAgent",
        model="gemini-2.5-flash",
        description="Composes prompts and drives image generation.",
        instruction=prompts.ART_DIRECTOR_PROMPT,
    )
