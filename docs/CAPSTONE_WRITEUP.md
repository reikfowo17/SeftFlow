# SeftFlow Copilot - Capstone Writeup

**Track: Freestyle**

## Problem

Solo sellers and indie creators need product posters and marketing copy fast, but hiring a designer is slow and expensive, and general image tools do not understand a product catalog. They juggle product data, reference images, copy, and poster iterations across disconnected tools.

## Why agents

SeftFlow already had a solid backend for products, copy, image sessions, posters, and a gallery. The missing piece was an interface that lets a non-technical creator describe an outcome ("create this product, write casual copy, render a hero image, save the best one") and have the system carry out the multi-step workflow. That is an agent problem: intent routing, tool use, and a transparent trace, not another form.

## Architecture

The Copilot is a Google ADK multi-agent system:

- **OrchestratorAgent** (Gemini 2.5 Flash) routes each turn.
- **CopywriterAgent** handles copy; **ArtDirectorAgent** handles imagery.
- **Function tools** wrap existing `application/` use cases, so there is no duplicated business logic.

The same tools are exposed twice: as an in-app SSE chat (`POST /api/agent/chat`, surfaced at `/copilot`) and as a Model Context Protocol stdio server for external clients (Claude Desktop, Codex CLI, Cursor). A portable `skills/seftflow/` skill wires Codex/Claude to the local MCP server. See `docs/AGENT_ARCHITECTURE.md` for diagrams.

## Demo

Prompt: *"Create a new product 'Summer T-shirt', write casual English copy, render a 1024x1024 hero image, then save the best result to the gallery."*

The trace panel shows each tool call (`create_product`, `generate_copy`, `generate_image`, `add_to_gallery`), and the results land in the database and gallery.

## Build

- **Backend**: FastAPI, SQLAlchemy, Alembic, Dramatiq (Redis), PostgreSQL.
- **Agent**: Google ADK + `google-genai`; MCP Python SDK for the server.
- **Frontend**: React + Vite; a Copilot page streams SSE events and renders the tool trace.
- **Reuse over rebuild**: tools call the same use cases as the UI; the agent layer is thin.

## Security

Every tool runs behind the existing admin session. A prompt-injection guard rejects dangerous arguments (shell, SQL, path traversal). Destructive operations stay gated by the `deletion_enabled` flag. Each turn is capped at 4 tool calls with a Redis-backed per-session rate limit.

## Lessons

- Wrapping existing use cases as function tools kept the agent honest and testable; a deterministic fallback made CI possible without an LLM key.
- Exposing one tool surface through both HTTP and MCP avoided drift and doubled the integration story for near-zero extra code.
- Guardrails are cheap to add early and hard to retrofit; binding tools to the existing auth session was the single highest-leverage safety decision.