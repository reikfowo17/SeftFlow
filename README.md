<p align="center">
  <img src="docs/assets/seftflow-brand-concept.png" alt="SeftFlow brand concept: product card connected to AI copy and image workflow nodes" width="168">
</p>

# SeftFlow

SeftFlow is an open-source, self-hosted product creative workspace for solo merchants and small teams. Its core flow covers product information, reference images, AI copywriting, AI/template posters, iterative image sessions, a generated image gallery, and a visual workflow.

The current form is a private single-admin instance. A self-hosted deployment requires PostgreSQL, Redis, the backend API, Dramatiq worker, Web frontend, and usable text/image model providers.

## Problem

Solo sellers and indie creators need product marketing copy and posters fast, but hiring a designer is slow and expensive, and general-purpose image tools do not understand a product catalog. Creators end up juggling product data, reference images, copy drafts, and poster iterations across disconnected tools, with no single place that ties them together.

## Solution

SeftFlow is a self-hosted creative workspace with a built-in AI Copilot. A creator describes an outcome in plain language ("create this product, write casual copy, render a hero image, save the best one"), and a multi-agent system carries out the multi-step workflow on top of the same business logic the web UI uses. Every step is a visible tool call, so the creator keeps a transparent, auditable trace instead of a black box.

## Feature Overview

### Products / Workbench

- Single-admin access-key login with Cookie session access to backend APIs.
- Product list, paginated browsing, product creation, product detail workbench, and product deletion protected by a global switch; the mobile product list uses cards and floating pagination.
- Node canvas for product information, reference images, copy nodes, and image-generation nodes.
- Desktop canvas interactions: mouse-wheel zoom, drag panning on blank canvas, node dragging, node connections, edge deletion, Ctrl/Cmd/Shift multi-select, and Shift box selection.
- The mobile product workbench keeps the canvas as the main surface and provides Browse, Edit, and Select modes; it supports one-finger pan, node tap selection, two-finger zoom, touch node dragging, touch edge creation, and tap-based multi-select.
- The mobile bottom toolbar opens workflow run, Single node, Templates, Details, Runs, and Library entrypoints, with panel content shown from a bottom sheet.
- Full-canvas templates for product creation; built-in node-group templates and user node-group templates for adding flows inside the workbench.
- Product source images, reference images, and iterative image-session references support click-to-select and drag-and-drop upload, protected by MIME, size, pixel, and count limits.
- Reference image nodes are single-image slots. Manual upload or upstream generation replaces the current image, while old assets stay in product history/assets.
- Copy nodes support generation, editing, confirmation, and history; current outputs are editable structured copy used by later image generation.
- Image-generation nodes only trigger and configure generation; results are written into connected downstream reference image nodes and previewed/downloaded from the reference image or Library sidebar.

### Text / Image Generation

- Standalone image sessions support reference uploads, base image selection from history, iterative generation, multiple-candidate comparison, and a mobile main-view/drawer/bottom-sheet layout.
- Mobile image chat uses a top bar for the session drawer, current session title/rename, and history drawer; generation status, the current result, and provider notes remain in the main view.
- Sessions open from the left drawer for create, select, and delete actions; branch/candidate history opens from the narrow right drawer, and tapping a completed image selects it as the current result and next base image.
- The bottom action bar always exposes the generation entry. After a completed result is selected, it also exposes download and send-to-gallery. The bottom generation sheet contains Generation / Advanced tabs, product linking, product/session references, prompt, size, candidate count, and image tool parameters.
- Running state includes queue position, lightweight status refresh, candidate progress, failure reasons, cancel, and retry.
- Generated images can be downloaded, sent to the gallery, saved as product reference images, or saved as product main-image references.

### Gallery

- `/gallery` centrally stores generated image results.
- Entries keep source session, linked product, prompt, size, model, and download entrypoint.

### Configuration and Runtime

- `/settings` supports runtime business overrides: provider, model, image size, image tool parameters, prompt templates, upload limits, global concurrency, business deletion switch, and more.
- Image tool parameters can control advanced fields sent to the Responses `image_generation` tool, including allowed fields, quality, output format, compression, background, moderation, action, input fidelity, partial images, and provider `n`. Responses background mode is enabled by default and falls back to synchronous requests when unsupported.
- Secret fields are not echoed back. The settings page is protected by an independent `SETTINGS_ACCESS_TOKEN` secondary unlock.
- Copy, poster, product workflow, and iterative image generation are dispatched through Dramatiq + Redis, with PostgreSQL as state storage.
- API/worker startup recovers unfinished copy/poster jobs, product workflows, and iterative image tasks.
- Running product workflows and iterative image generation only poll lightweight status responses, then refresh full details after completion.

### In-Product Help

- The top navigation provides a `/help` page.
- Help is organized by real product areas: getting started, canvas workbench, gallery, text/image generation, and settings.
- The help page includes left-side page navigation, a local table of contents, previous/next links, and local full-text search.

### Preview

![Product list example](images/preview1.png)

![Product workbench example](images/preview2.png)

![New product example](images/preview3.png)

![Image-to-image panel example](images/preview4.png)

![Dark mode and English mode example](images/preview5.png)

## AI Agent (Google ADK)

SeftFlow ships a **Copilot** built on the [Google Agent Development Kit (ADK)](https://google.github.io/adk-docs/). It is a small multi-agent system layered on top of the existing `application/` use cases, so the agent reuses the same business logic as the web UI instead of duplicating it.

- **OrchestratorAgent** (`gemini-2.5-flash`) routes a user turn to the right specialist.
- **CopywriterAgent** focuses on short-form product copy.
- **ArtDirectorAgent** composes prompts and drives image generation.
- **Function tools** (`agent/tools.py`) wrap `create_product`, `list_products`, `generate_copy`, `generate_image`, `add_to_gallery`, `run_product_workflow`, and `get_workflow_status`.

The Copilot is available at `/copilot` in the web app and via `POST /api/agent/chat` (SSE streaming). If no `GEMINI_API_KEY` is set, the orchestrator runs in a deterministic fallback mode that still exercises tools and guards, which keeps local development and CI free of external LLM calls.

```mermaid
flowchart LR
    U[User / Copilot UI] -->|/api/agent/chat SSE| O[OrchestratorAgent]
    O --> C[CopywriterAgent]
    O --> A[ArtDirectorAgent]
    O --> T[Function tools]
    C --> T
    A --> T
    T --> UC[application/ use cases]
    UC --> DB[(PostgreSQL)]
    UC --> Q[(Redis / Dramatiq)]
```

## MCP Server

The same tools are exposed over the Model Context Protocol (stdio) so external clients (Claude Desktop, Codex CLI, Cursor) can drive SeftFlow:

```bash
just mcp-serve   # or: python -m productflow_backend.mcp_server
```

It publishes 7 tools plus 2 read-only resources (`productflow://products`, `productflow://gallery`). An optional Docker service is available under the `mcp` profile:

```bash
docker compose --profile mcp up productflow-mcp
```

## SeftFlow Skill (Codex CLI / Claude)

`skills/seftflow/` packages a portable agent skill so Codex CLI or Claude can talk to a local SeftFlow through the MCP server.

```bash
just skill-install   # copies the skill into $CODEX_HOME/skills/seftflow
```

See `skills/seftflow/SKILL.md` for the invocation template and MCP client config.

## Security Model

- **Auth-bound tools**: every agent/MCP call runs behind the existing `require_admin` session dependency.
- **Prompt-injection guard** (`agent/guards.py`): rejects tool arguments containing `rm -rf`, SQL `DROP/DELETE/TRUNCATE`, path traversal, and `exec`/`os.system`.
- **Deletion flag**: destructive operations stay gated by the backend `deletion_enabled` setting.
- **Quotas + rate limit**: a per-turn cap of 4 tool calls and a Redis-backed token bucket (with in-memory fallback) throttle each session.

## Current Boundaries

SeftFlow does not currently provide multi-user/multi-tenant support, team permissions, payments, hosted account systems, automatic ad placement/listing, video generation, Kubernetes/Helm/released container images, or other production orchestration packages. The in-repository Docker Compose self-hosting path is available.

## Product Entry Points and Docs

- In-product help: top navigation **Help**, route `/help`
- New user guide reference: `docs/USER_GUIDE.md`
- Architecture guide: `docs/ARCHITECTURE.md`
- Current architecture health review: `docs/ARCHITECTURE_HEALTH_REVIEW.md`
- Roadmap: `docs/ROADMAP.md`
- Brand assets: `docs/assets/seftflow-brand-concept.png`, `docs/assets/seftflow-mark.svg`
- Web metadata / favicon assets: `web/public/seftflow-brand-concept.png`, `web/public/seftflow-mark.svg`

## Tech Stack

- Backend: Python 3.12, FastAPI, SQLAlchemy, Alembic, Dramatiq, Redis, PostgreSQL, Pillow, OpenAI Python SDK.
- Frontend: React 19, Vite, TypeScript, React Router, TanStack Query, Tailwind CSS 4.
- Local development entrypoint: root `justfile`; if `just` is unavailable, raw commands are listed below.
- Docs: `docs/PRD.md`, `docs/USER_GUIDE.md`, `docs/ARCHITECTURE.md`, `docs/ARCHITECTURE_HEALTH_REVIEW.md`, `docs/ROADMAP.md`.

## Repository Structure

```text
SeftFlow/
  README.md
  LICENSE
  SECURITY.md
  .env.example
  .env.dev.example
  docker-compose.yml
  .dockerignore
  justfile
  scripts/
    release.sh
    with_dev_env.sh
  docs/
    PRD.md
    USER_GUIDE.md
    ARCHITECTURE.md
    ARCHITECTURE_HEALTH_REVIEW.md
    ROADMAP.md
    assets/
      seftflow-brand-concept.png
      seftflow-mark.svg
  backend/
    Dockerfile
    pyproject.toml
    alembic.ini
    alembic/versions/
    src/productflow_backend/
    tests/
  web/
    Dockerfile
    nginx.conf
    package.json
    public/
      seftflow-brand-concept.png
      seftflow-mark.svg
    src/
  skills/
    productflow/
```


skills/seftflow/ packages the portable agent skill (SKILL.md + MCP launcher) so Codex CLI or Claude can drive a local SeftFlow instance through the MCP server.

## Quick Start: One-Command Self-Hosting with Docker Compose

This path is for single-machine self-hosted deployment. The default configuration can run the basic flow. After configuring real model providers, persistent storage, and reverse proxy/HTTPS, it can be used as a foundation for small-scale production. The host only needs Docker / Docker Compose; Python, `uv`, Node, `pnpm`, and `just` are not required. Compose builds and starts PostgreSQL, Redis, the backend API, the Dramatiq worker, and the Web static site.

### 1. Copy and edit environment variables

```bash
cp .env.example .env
```

At minimum, change these values:

- `ADMIN_ACCESS_KEY`: admin key used to log in to the backend UI.
- `SETTINGS_ACCESS_TOKEN`: secondary unlock token for the settings page; it must be different from the login key.
- `SESSION_SECRET`: long random string used to sign session cookies.
- `POSTGRES_PASSWORD`: PostgreSQL password; Compose uses it to build the in-container `DATABASE_URL`.

The default provider is `mock`, and `POSTER_GENERATION_MODE=template`, so you can complete basic flows such as creating products, generating copy, and rendering template posters without real model keys. Read "Model and Provider Configuration" before switching to real models.

### 2. Build and start everything

```bash
docker compose up -d --build
```

Do not append service names to this command; adding a service name starts only that service. The complete self-hosted stack should start all services together.

Compose starts these services by default:

- PostgreSQL: service name `productflow-postgres`, Compose volume `productflow-postgres-data`, host port `${POSTGRES_HOST_PORT:-15432}`.
- Redis: service name `productflow-redis`, AOF persistence volume `productflow-redis-data`, host port `${REDIS_HOST_PORT:-16379}`.
- Backend API: service name `productflow-backend`, host port `${APP_HOST_PORT:-29280}`.
- Dramatiq worker: service name `productflow-worker`, sharing database, Redis, and storage volumes with the API.
- Web: service name `productflow-web`, nginx static service, host port `${WEB_PORT:-29281}`.

If a port is already occupied, edit `APP_HOST_PORT`, `WEB_PORT`, `POSTGRES_HOST_PORT`, or `REDIS_HOST_PORT` in `.env`, then run `docker compose up -d --build` again. Containers still connect to one another through service names, so you do not need to change application `DATABASE_URL` / `REDIS_URL`.

The in-container application uses Compose network service names:

```text
DATABASE_URL=postgresql+psycopg://productflow:<POSTGRES_PASSWORD>@productflow-postgres:5432/productflow
REDIS_URL=redis://productflow-redis:6379/0
STORAGE_ROOT=/app/storage
```

At runtime, container `STORAGE_ROOT` is fixed to `/app/storage`; do not write host paths into it. By default, uploaded and generated files are stored in the Docker named volume `productflow-storage` and persist across container restarts.

When migrating from an older systemd production environment, if you already have a production file directory such as `/home/cot/SeftFlow-release/shared/storage`, set this host-only variable in `.env` to reuse it:

```bash
STORAGE_HOST_PATH=/home/cot/SeftFlow-release/shared/storage
```

`STORAGE_HOST_PATH` is only the host path used by the Compose bind mount. API/worker containers still use `STORAGE_ROOT=/app/storage`. If empty or unset, Compose uses the `productflow-storage` named volume. Do not run `docker compose down -v` for normal updates, and do not delete Docker volumes just to switch storage mounts. To return to the named volume, remove `STORAGE_HOST_PATH` and run `docker compose up -d`.

### 3. Database migration

The `productflow-backend` startup command first runs:

```bash
alembic upgrade head
```

`uvicorn` starts only after migrations succeed. After upgrading code, if you need to rerun migrations manually:

```bash
docker compose run --rm productflow-backend alembic upgrade head
```

### 4. Access and health checks

With default ports:

```bash
docker compose ps
curl http://127.0.0.1:29280/healthz
curl http://127.0.0.1:29281/api/healthz
```

If you changed ports in `.env`, replace them with the corresponding values:

```bash
curl "http://127.0.0.1:<APP_HOST_PORT>/healthz"
curl "http://127.0.0.1:<WEB_PORT>/api/healthz"
```

Expected API response:

```json
{"status":"ok"}
```

Default Web entrypoint: `http://127.0.0.1:29281` (or the `WEB_PORT` from `.env` if changed). Log in with `ADMIN_ACCESS_KEY` from `.env`. The Web image serves Vite-built static assets through nginx, and nginx reverse-proxies same-origin `/api/*` requests to `productflow-backend:29280`.

### 5. Logs, stop, and cleanup

```bash
docker compose logs -f productflow-backend productflow-worker productflow-web
docker compose down
```

Stopping services does not delete data volumes. Only run this when you are sure you want to clear the database, Redis, and storage:

```bash
docker compose down -v
```

## Local Development Path

Use the local development path when changing code and using hot reload.

### 1. Prepare tools

Required on the host:

- Python 3.12+
- `uv`
- Node.js 20+ or a compatible version
- `pnpm`
- Docker / Docker Compose
- `just` (optional; raw commands are also listed below)

### 2. Copy environment variables

```bash
cp .env.example .env
cp .env.dev.example .env.dev
cp web/.env.example web/.env
```

The `DATABASE_URL` / `REDIS_URL` in `.env.example` target the Compose container network. Local hot-reload development commands use `.env.dev` to connect through host `localhost:${POSTGRES_HOST_PORT:-15432}` and `localhost:${REDIS_HOST_PORT:-16379}`. At minimum, change these values in `.env` / `.env.dev` to your own random values:

- `ADMIN_ACCESS_KEY`: admin key used to log in to the backend UI.
- `SETTINGS_ACCESS_TOKEN`: secondary unlock token for the settings page; it must be different from the login key.
- `SESSION_SECRET`: long random string used to sign session cookies.
- `POSTGRES_PASSWORD`: local PostgreSQL password; keep it consistent with the password in `.env.dev`'s `DATABASE_URL`.

`.env.dev.example` uses development ports, Redis DB 1, and `backend/storage-dev`. The database name matches the default `docker-compose.yml`. If you use a separate development database, create it in PostgreSQL first, then adjust `.env.dev`'s `DATABASE_URL`. Local development storage is isolated from production Compose storage: `just backend-run` / `just backend-worker` and their raw equivalents read `STORAGE_ROOT=./backend/storage-dev` from `.env.dev`. Do not start local development processes by shell-sourcing production `.env` or importing production `STORAGE_HOST_PATH`.

### 3. Start development dependencies only

For local hot reload, use Compose only for PostgreSQL and Redis. The API, worker, and Web are started by host commands in the next step. The complete self-hosted stack uses `docker compose up -d --build` from the previous section.

```bash
docker compose up -d productflow-postgres productflow-redis
```

### 4. Install dependencies and migrate the database

With `just`:

```bash
just backend-install
just web-install
just backend-migrate
```

Without `just`:

```bash
uv sync --directory backend --extra dev
pnpm --dir web install
bash scripts/with_dev_env.sh uv run --directory backend alembic upgrade head
```

### 5. Start backend, worker, and frontend

Run these in three terminals. With `just`:

```bash
just backend-run
just backend-worker
just web-dev
```

Without `just`:

```bash
bash scripts/with_dev_env.sh bash -lc 'uv run --directory backend uvicorn productflow_backend.main:app --reload --host 0.0.0.0 --port "${APP_PORT:-29282}"'
bash scripts/with_dev_env.sh uv run --directory backend dramatiq --processes 2 --threads 4 productflow_backend.workers
bash scripts/with_dev_env.sh bash -lc 'web_port="${WEB_PORT:-29283}"; api_target="${VITE_DEV_PROXY_TARGET:-http://127.0.0.1:${APP_PORT:-29282}}"; VITE_API_BASE_URL= VITE_DEV_PROXY_TARGET="$api_target" pnpm --dir web dev -- --host 0.0.0.0 --port "$web_port" --strictPort'
```

Default development ports come from `.env.dev.example`:

- API: `http://localhost:29282`
- Web: `http://localhost:29283`

Open the Web page and log in with `ADMIN_ACCESS_KEY`. The top navigation provides **Products / Workbench**, **Image chat**, **Gallery**, **Help**, and **Settings**.

### 6. Development health check

```bash
curl http://127.0.0.1:29282/healthz
```

Expected response:

```json
{"status":"ok"}
```

## Model and Provider Configuration

SeftFlow configures text and image capabilities separately. Infrastructure configuration (database, Redis, session, admin key) is still read only from environment variables. Business configuration can be written to the database from the frontend `/settings` page and override environment defaults.

The login gate `admin_access_required` is enabled by default: normal workspace pages and private APIs require login with `ADMIN_ACCESS_KEY` first. Administrators can disable this gate after the secondary `/settings` unlock, allowing the ordinary workspace/API to be used without the admin key. `ADMIN_ACCESS_KEY` still must remain in the environment for future re-enabling, and `SETTINGS_ACCESS_TOKEN` always protects settings reads and writes independently.

Business hard deletion is disabled by default: when `DELETION_ENABLED=false`, product deletion and iterative image-session deletion APIs return 403 so demo sites can preserve evidence for policy review. Workflow node/edge editing and reference-image deletion are not affected. To remove whole products or sessions, an administrator can explicitly enable "business deletion" in `/settings`, or enable the environment default.

Text providers:

- `TEXT_PROVIDER_KIND=mock`: local fake implementation for development and testing.
- `TEXT_PROVIDER_KIND=openai`: OpenAI Responses-compatible interface.
- Related variables: `TEXT_API_KEY`, `TEXT_BASE_URL`, `TEXT_BRIEF_MODEL`, `TEXT_COPY_MODEL`.

Image providers:

- `IMAGE_PROVIDER_KIND=mock`: local fake image implementation.
- `IMAGE_PROVIDER_KIND=openai_responses`: OpenAI Responses `image_generation` tool with reference image input. SeftFlow's current iterative image branch context is determined by the base image and reference images explicitly selected by the user; it does not automatically send the entire historical image chain to the provider.
- Related variables: `IMAGE_API_KEY`, `IMAGE_BASE_URL`, `IMAGE_GENERATE_MODEL`, `IMAGE_RESPONSES_BACKGROUND_ENABLED`, `IMAGE_GENERATION_MAX_DIMENSION`, `IMAGE_MAIN_IMAGE_SIZE`, `IMAGE_PROMO_POSTER_SIZE`.
- Advanced tool parameters: `IMAGE_TOOL_ALLOWED_FIELDS` controls which tool fields the frontend can show, the backend can persist, and the provider request can include. Optional defaults also include `IMAGE_TOOL_MODEL`, `IMAGE_TOOL_QUALITY`, `IMAGE_TOOL_OUTPUT_FORMAT`, `IMAGE_TOOL_OUTPUT_COMPRESSION`, `IMAGE_TOOL_BACKGROUND`, `IMAGE_TOOL_MODERATION`, `IMAGE_TOOL_ACTION`, `IMAGE_TOOL_INPUT_FIDELITY`, `IMAGE_TOOL_PARTIAL_IMAGES`, and `IMAGE_TOOL_N`.

Poster modes:

- `POSTER_GENERATION_MODE=template`: render with local templates/Pillow without calling an image model.
- `POSTER_GENERATION_MODE=generated`: send confirmed copy and product/reference images to the image provider to generate posters.

Prompt templates:

- The prompt group in `/settings` can override templates for product understanding, copy generation, workbench image generation, and iterative image generation.
- Put one-off requirements into copy/image nodes; update settings-page templates only for long-term shared tone or format.

## Common Commands

| Purpose | With `just` | Without `just` |
|---|---|---|
| Install backend dependencies | `just backend-install` | `uv sync --directory backend --extra dev` |
| Install frontend dependencies | `just web-install` | `pnpm --dir web install` |
| Apply development DB migration | `just backend-migrate` | `bash scripts/with_dev_env.sh uv run --directory backend alembic upgrade head` |
| Start development API | `just backend-run` | `bash scripts/with_dev_env.sh bash -lc 'uv run --directory backend uvicorn productflow_backend.main:app --reload --host 0.0.0.0 --port "${APP_PORT:-29282}"'` |
| Start Dramatiq worker | `just backend-worker` | `bash scripts/with_dev_env.sh uv run --directory backend dramatiq --processes 2 --threads 4 productflow_backend.workers` |
| Run backend pytest | `just backend-test` | `uv run --directory backend pytest` |
| Start Vite dev server | `just web-dev` | `bash scripts/with_dev_env.sh bash -lc 'web_port="${WEB_PORT:-29283}"; api_target="${VITE_DEV_PROXY_TARGET:-http://127.0.0.1:${APP_PORT:-29282}}"; VITE_API_BASE_URL= VITE_DEV_PROXY_TARGET="$api_target" pnpm --dir web dev -- --host 0.0.0.0 --port "$web_port" --strictPort'` |
| Run frontend lint | no just wrapper | `pnpm --dir web lint` |
| Run frontend unit tests | no just wrapper | `pnpm --dir web test:run` |
| TypeScript check + Vite build | `just web-build` | `pnpm --dir web build` |
| Release dry run | `just release-dry-run` | `DRY_RUN=1 bash scripts/release.sh` |
| Production update | `just release` | `bash scripts/release.sh` |

`just release` / `bash scripts/release.sh` is the Docker Compose production update entrypoint. It first runs `docker compose config --quiet`, then attempts to stop legacy user-level systemd services that may occupy ports `29280/29281` (`productflow-backend.service`, `productflow-worker.service`, `productflow-web.service`), then runs `docker compose up -d --build --remove-orphans` and checks backend `/healthz`, web `/healthz`, and web proxy `/api/healthz`. This process does not delete Docker volumes; do not use `docker compose down -v` for normal updates. To reuse files from an old systemd production setup, set `STORAGE_HOST_PATH=/home/cot/SeftFlow-release/shared/storage` in `.env` first. If you have already manually moved old services away, you can temporarily run `LEGACY_SYSTEMD_ACTION=skip bash scripts/release.sh`, or `LEGACY_SYSTEMD_ACTION=skip just release`.

`just release-dry-run` / `DRY_RUN=1 bash scripts/release.sh` only validates Compose configuration and prints the steps a real release would execute. It does not stop systemd services, build images, start containers, or switch running services.

## Main API Resources

The backend exposes REST APIs only. Main entrypoints include:

- `POST /api/auth/session`, `GET /api/auth/session`, `DELETE /api/auth/session`
- `/api/products`, `/api/products/{product_id}`, `/api/products/{product_id}/history`
- `/api/products/{product_id}/reference-images`, `/api/source-assets/{asset_id}`, `/api/source-assets/{asset_id}/download`
- `/api/copy-sets/{copy_set_id}`, `/api/copy-sets/{copy_set_id}/confirm`
- `/api/posters/{poster_id}/download`
- `/api/image-sessions`, `/api/image-sessions/{image_session_id}`, `/api/image-sessions/{image_session_id}/status`, `/api/image-session-assets/{asset_id}/download`
- `/api/gallery`
- `/api/generation-queue`
- `/api/products/{product_id}/workflow`, `/api/products/{product_id}/workflow/status`, `/api/products/{product_id}/workflow/run`, `/api/products/{product_id}/workflow/runs/{run_id}/cancel`
- `/api/workflow/canvas-templates`, `/api/workflow/user-template-groups`
- `/api/workflow-nodes/{node_id}`, `/api/workflow-edges/{edge_id}`
- `/api/settings`, `/api/settings/lock-state`, `/api/settings/unlock`, `/api/settings/runtime`

This list contains common resource entrypoints, not a complete OpenAPI reference. Operation endpoints also include iterative image generate/cancel/retry/save-to-gallery, workflow retry, template insertion, and user-template management.

## Open Source and Security Boundaries

- License: MIT, see `LICENSE`.
- Security reporting: see `SECURITY.md`.
- Do not commit `.env`, `web/.env`, local storage, build outputs, caches, logs.
- Real provider API keys should only be stored in local environment variables or private deployment configuration. Do not write them into issues, PRs, or documentation examples.
