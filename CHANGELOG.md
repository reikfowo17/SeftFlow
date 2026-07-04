# Changelog

All notable changes for SeftFlow are recorded here.

## Unreleased

### Added

- AI Copilot built on Google ADK: OrchestratorAgent + CopywriterAgent + ArtDirectorAgent, exposed at `/copilot` and via `POST /api/agent/chat` (SSE).
- MCP stdio server (`productflow_backend.mcp_server`) exposing 7 tools and 2 read-only resources; optional `mcp` Docker profile and `just mcp-serve`.
- Portable `skills/seftflow/` skill for Codex CLI / Claude with `just skill-install`.
- Agent security guards: auth-bound tools, prompt-injection guard, per-turn tool-call cap, Redis rate limit.
- Capstone docs: `docs/AGENT_ARCHITECTURE.md`, `docs/CAPSTONE_WRITEUP.md`, `docs/CAPSTONE_VIDEO_SCRIPT.md`.
- Backend tests: `test_agent_tools.py`, `test_agent_orchestrator.py`, `test_agent_security_guards.py`, `test_mcp_server.py`.

### Changed

- Localized the app to English only (`en-US`); removed `zh-CN` / `ja-JP` locales and translated default prompts and docs.

### Fixed

- Repaired backend modules where an earlier localization pass dropped `items` identifiers, leaving the backend unimportable.

## 0.1.0 - 2026-05-02

Initial public self-hosted release for SeftFlow. This entry is the durable release record for `v0.1.0`.

### Added

- Single-admin, self-hosted product creative workspace with access-key login and Cookie-session API access.
- Product list, product creation, product detail workbench, source/reference image upload, and controlled download routes.
- SeftFlow workbench DAG for product context, reference images, copy generation, and image generation.
- Persistent workflow nodes, edges, runs, node-run state, failure reasons, startup recovery, and lightweight workflow status polling.
- Copy generation, editable copy fields, copy confirmation, product history, template poster output, and remote image-provider poster output.
- Reference-image single-slot semantics: manual uploads or generated-image fills replace the current slot image while older assets remain in product history/assets.
- Standalone iterative image sessions with durable generation tasks, queue position, retry/failure state, multiple generated candidates, and product attachment.
- Generated image gallery at `/gallery` for saved iterative-image results with source, product, prompt, size, model, and download metadata.
- Runtime settings page for provider/model selection, image sizes, upload limits, retry/concurrency controls, prompt templates, login gate, business deletion switch, and secrets that are not echoed back.
- Docker Compose self-hosting path for PostgreSQL, Redis, FastAPI backend, Dramatiq worker, and nginx-served Web build.
- Release helpers: `just release-dry-run` for safe validation and `just release` for Compose rebuild/start plus health checks.
- Chinese and English public docs for README, PRD, architecture, roadmap, and user guide.

### Release Boundaries

- SeftFlow 0.1.0 is not a hosted SaaS, public registration system, multi-tenant platform, or team-permission product.
- No hosted model accounts, billing, store authorization, automatic ad/listing pipeline, or video-generation workflow is included.
- No published container image, Helm chart, Kubernetes manifest, or cloud deployment package is included in `v0.1.0`.
- Docker volumes are not deleted by release helpers; `docker compose down -v` is only a manual reset command.

### Verification

Release preparation for `v0.1.0` used the lightweight documentation and build gates:

- `just release-dry-run`
- `just backend-test`
- `just web-build`
- `git diff --check`

The production update entrypoint remains `just release`, which should only be run intentionally on the deployment host.
