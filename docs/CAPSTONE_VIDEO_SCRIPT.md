# Capstone Video Script (5 minutes)

Track: Freestyle. Record with OBS, upload to YouTube (public), hide API keys on screen.

## 0:00-0:30 - Problem
- Show a solo seller with a product photo and no design help.
- "Creators need product posters and copy fast, without hiring a designer."

## 0:30-1:30 - Why agents
- Show the existing SeftFlow UI (products, copy, image sessions, gallery).
- "The backend already does the work. What was missing is an agent that turns one request into the full multi-step workflow."
- Introduce the three agents: Orchestrator, Copywriter, ArtDirector.

## 1:30-3:00 - Architecture
- Walk the `docs/AGENT_ARCHITECTURE.md` mermaid diagram.
- Highlight: tools wrap existing use cases; same tools exposed via HTTP SSE and MCP stdio.
- Show `agent/guards.py` briefly (prompt-injection guard, tool-call cap, rate limit).

## 3:00-5:00 - Live demo
- Open `/copilot`.
- Prompt: "Create a new product 'Summer T-shirt', write casual English copy, render a 1024x1024 hero image, then save the best result to the gallery."
- Show the streamed trace: create_product -> generate_copy -> generate_image -> add_to_gallery.
- Open the gallery to confirm the saved result.
- Switch to Codex CLI: run the SeftFlow skill against the local MCP server and issue one command to prove the same tools work externally.

## Closing (within the 5:00 window)
- Recap stack, security model, and 5-command Docker reproduction.
- Mention future work (persistent sessions, richer ArtDirector iteration).