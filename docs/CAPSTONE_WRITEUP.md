# SeftFlow Copilot — A Self-Hosted AI Design Partner for Solo Sellers

**Subtitle:** An ADK multi-agent Copilot and MCP server layered on a real product-creative workspace, so a single request becomes a full copy-and-image workflow.

**Track:** Freestyle

---

## 1. Problem

Solo sellers and indie creators live and die by their product listings, but the work behind a good listing is fragmented and slow. To publish one product they bounce between a spreadsheet for product data, a folder of reference photos, a chat tool for copywriting, and a separate image generator for a hero shot. Hiring a designer is expensive and slow; general-purpose image tools produce pretty pictures that ignore the actual catalog (wrong product name, wrong category, no consistency across a store).

The core pain is not "generate an image" or "write a caption" in isolation. It is the multi-step, stateful workflow: create the product, write copy in the right tone, render an on-brand hero image, iterate, and save the best result somewhere reusable. Today that workflow lives in a human's head and a dozen browser tabs.

## 2. Why Agents

This is an agent problem, not another form or another button. The task requires **intent routing** (is this a copy request, an image request, or a full brief?), **sequential tool use** with state passed between steps (a product id feeds copy, copy and references feed image generation, a generated asset feeds the gallery), and a **transparent trace** so a non-technical creator can trust and audit what happened. A single-shot prompt to an LLM cannot own that loop; a chain of agents with real tools can.

SeftFlow already had a working backend for products, copy, image sessions, posters, and a gallery. The missing piece was an interface that lets a creator say "create a Summer T-shirt product, write casual English copy, render a 1024×1024 hero image, and save the best one" and have the system carry out each step against the same business logic the web UI uses.

## 3. Solution

SeftFlow is an open-source, self-hosted product-creative workspace with a built-in **Copilot**. The Copilot is a Google ADK multi-agent system that plans a brief and drives function tools which wrap the existing `application/` use cases. The same tool surface is exposed two ways: an in-app SSE chat at `/copilot` (`POST /api/agent/chat`) and a Model Context Protocol (MCP) stdio server for external clients such as Codex CLI, Claude Desktop, and Cursor. A portable agent skill (`skills/seftflow/`) wires those external clients to a local instance.

The design principle throughout is reuse over rebuild: the agent layer is thin, and every tool calls the same use case the web app already trusts, so there is one source of truth for business logic and no drift between the UI and the agent.

## 4. Course Concepts Demonstrated

The project applies five of the six course concepts (three are required):

| Concept | Where | Evidence |
| --- | --- | --- |
| **Multi-agent system (ADK)** | Code | `agent/orchestrator.py`, `agent/subagents.py` — a root `LlmAgent` with two specialist sub-agents. |
| **MCP Server** | Code | `mcp_server/server.py` — stdio server publishing 7 tools + 2 read-only resources. |
| **Security features** | Code | `agent/guards.py` — prompt-injection guard, per-turn tool-call quota, Redis rate limit; all tools behind `require_admin`. |
| **Agent skills** | Code | `skills/seftflow/SKILL.md` + `scripts/run_mcp.py` launcher for Codex CLI / Claude. |
| **Deployability** | Video/Code | `docker-compose.yml`, `justfile`, backend and web `Dockerfile`s; 5-command local reproduction. |

## 5. Architecture

```
User / Copilot UI
      │  POST /api/agent/chat (SSE)
      ▼
OrchestratorAgent (Gemini 2.5 Flash)
   ├── CopywriterAgent   (short-form product copy)
   ├── ArtDirectorAgent  (prompt composition + image generation)
   └── Function tools ───► application/ use cases ──► PostgreSQL
                                              └──► Redis / Dramatiq
```

**Orchestrator** (`agent/orchestrator.py`). `build_orchestrator()` constructs a root ADK `LlmAgent` named `SeftFlowCopilot` on `gemini-2.5-flash`, with the two sub-agents attached. ADK is imported lazily, so the rest of the app stays importable without the `google-adk` dependency. `run_agent_turn()` is the public entrypoint the FastAPI route calls; it yields `AgentTurnEvent`s (`text`, `tool_call`, `tool_result`, `error`, `done`) that the route serializes as Server-Sent Events.

**Sub-agents** (`agent/subagents.py`). `CopywriterAgent` handles titles, selling points, and tone; `ArtDirectorAgent` composes a single concrete image prompt and drives generation, defaulting to 1024×1024 and referencing a prior image session when iterating. Prompts live in `agent/prompts.py` and encode the routing rules and constraints (e.g. the 4-tool-call cap).

**Function tools** (`agent/tools.py`). `SeftFlowTools` is bound to a caller session for quota/rate tracking. It exposes seven tools — `list_products`, `get_workflow_status`, `create_product`, `generate_copy`, `generate_image`, `add_to_gallery`, `run_product_workflow` — each of which calls an existing use case (`application.use_cases`, `application.image_sessions`, `application.gallery`). `build_tool_map()` returns a name-indexed mapping reused by both the ADK bindings and the MCP server, which is what keeps the two transports in lockstep.

**Deterministic fallback.** When no `GEMINI_API_KEY`/`GOOGLE_API_KEY` is set (or ADK is not installed), `build_orchestrator()` returns `None` and the turn runs through `_fallback_turn()` — a keyword router that still selects a plausible tool and returns a trace-only response. This keeps local development, MCP smoke checks, and CI free of external LLM calls, which made the agent testable from day one.

**HTTP transport** (`presentation/routes/agent.py`). `POST /api/agent/chat` streams the turn as SSE and sets an `agent_session` cookie so a session id persists across turns. The whole router is mounted behind `Depends(require_admin)`. Sessions are in-memory via ADK's `InMemorySessionService` in v1; persistent history is planned, not implemented.

**MCP transport** (`mcp_server/server.py`). A stdio `Server` publishes the same seven tools (via `TOOL_SCHEMAS`) plus two read-only resources, `productflow://products` and `productflow://gallery`. Each MCP tool call builds a fresh `SeftFlowTools` and dispatches through the shared `build_tool_map()`. An optional Docker service runs it under the `mcp` compose profile.

## 6. Security

Security was designed in early because it is cheap to add up front and painful to retrofit:

- **Auth-bound tools.** Every agent and MCP call runs behind the existing `require_admin` session dependency; there is no unauthenticated agent surface.
- **Prompt-injection guard** (`agent/guards.py`). Tool arguments are checked against patterns for `rm -rf`, SQL `DROP TABLE` / `DELETE FROM` / `TRUNCATE`, path traversal (`../`), and `exec(` / `os.system`. A match raises `GuardError` and the turn returns a clean error event.
- **Per-turn tool-call quota.** `TurnBudget` caps a single turn at 4 tool calls, bounding blast radius and runaway loops.
- **Rate limiting.** `redis_rate_limit()` uses a Redis-backed counter (60 calls/min per session) and falls back to an in-process token bucket if Redis is unavailable.
- **Destructive-action gate.** Deletion stays governed by the backend `deletion_enabled` flag, independent of the agent.
- **Secret hygiene.** Secrets live only in gitignored `.env` files; the public repo ships `.env.example` placeholders, and settings never echo secret fields.

## 7. The Build

**Backend.** Python 3.12, FastAPI, SQLAlchemy, Alembic, Dramatiq on Redis, PostgreSQL, Pillow. Copy, poster, workflow, and iterative image jobs are dispatched through Dramatiq + Redis with Postgres as state storage; API/worker startup recovers unfinished jobs.

**Agent stack.** Google ADK (`google-adk`) + `google-genai` for the LLM agents, and the MCP Python SDK for the stdio server.

**Frontend.** React 19, Vite, TypeScript, React Router, TanStack Query, Tailwind CSS 4. The `/copilot` page streams SSE events and renders each tool call and result as a visible trace.

**Reuse over rebuild.** Because the tools call the same use cases as the UI, adding the agent required no duplicated business logic — the agent layer is deliberately thin.

## 8. Demo Walkthrough

Prompt: *"Create a new product 'Summer T-shirt', write casual English copy, render a 1024×1024 hero image, then save the best result to the gallery."*

1. The Orchestrator plans the brief and calls `create_product` → a product row is created.
2. It routes copy work to the Copywriter path and calls `generate_copy`.
3. It routes imagery to the Art Director path and calls `generate_image`, which creates an image session and submits a generation task.
4. On a good result it calls `add_to_gallery` to persist the asset.

The Copilot trace panel shows each `tool_call` and `tool_result` inline, and the outputs land in the database and the `/gallery` view. Switching to Codex CLI with the `skills/seftflow` skill exercises the exact same tools over MCP, proving the surface works both in-app and externally.

## 9. Deployability

The repository ships a one-command self-hosting path via Docker Compose (Postgres, Redis, backend API, Dramatiq worker, nginx-served web, and an optional `mcp` profile). A `justfile` wraps the common tasks. Local reproduction is five steps:

```bash
git clone https://github.com/reikfowo17/SeftFlow && cd SeftFlow
cp .env.example .env          # fill secrets + GEMINI_API_KEY (optional)
docker compose up -d          # Postgres + Redis (+ backend/worker/web)
just backend-migrate          # apply Alembic schema
just backend-run              # API; then `just web-dev` for the UI
```

Without a Gemini key, the Copilot runs in deterministic fallback mode, so the app is fully explorable offline.

## 10. Journey, Honest Boundaries, and Next Steps

The biggest lesson was that wrapping existing use cases as function tools kept the agent honest and testable: the deterministic fallback let the whole agent path run in CI without a key, and exposing one tool surface through both HTTP and MCP doubled the integration story for near-zero extra code.

I have kept the writeup honest about current limits. `generate_copy` and `run_product_workflow` return the latest state plus a hint that full (re)generation is driven by the workflow engine and the product workbench route, rather than doing heavy generation inside the tool. Agent sessions are in-memory in v1, so conversation history is not persisted across restarts. SeftFlow is a single-admin, self-hosted instance; multi-tenant accounts, payments, and hosted orchestration are out of scope.

Planned next steps: persistent agent sessions, a richer Art Director iteration loop (multi-candidate compare inside a turn), and moving copy regeneration fully behind a tool call rather than a hint.

## 11. Links

- **Code:** https://github.com/reikfowo17/SeftFlow
- **Architecture detail:** `docs/AGENT_ARCHITECTURE.md`
- **Video script:** `docs/CAPSTONE_VIDEO_SCRIPT.md`
- **License:** MIT