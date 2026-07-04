---
name: seftflow
description: Use when the user wants to create a product, write product copy, or render a product poster/hero image through a local SeftFlow instance. Drives the SeftFlow MCP server (stdio) which exposes product, copy, image, and gallery tools backed by the same application use cases as the app.
---

# SeftFlow Skill

Drive a local [SeftFlow](https://github.com/yuqie6/SeftFlow) instance from Codex CLI or Claude as an MCP client. SeftFlow is a personal AI design partner for solo sellers and indie creators who need product posters fast.

## When to use

Use this skill when the user asks to:

- Create a product record ("add a product called Summer Tee").
- List or inspect existing products and their workflow history.
- Generate or refine short-form product copy.
- Render a poster / hero image and save the best result to the gallery.

Do **not** use it for generic image generation unrelated to a SeftFlow catalog, or when no local SeftFlow backend is reachable.

## Prerequisites

1. A running SeftFlow backend (Postgres + Redis via `docker compose up -d`, migrations applied with `just backend-migrate`).
2. Backend env available (`.env` copied from `.env.example`). The MCP server reuses the same `DATABASE_URL`, `REDIS_URL`, and provider settings.
3. The `mcp` Python SDK installed in the backend environment (`uv add mcp` inside `backend/`).

## How to run

The MCP server speaks stdio. Register it with your MCP client, or launch it directly:

```bash
python skills/seftflow/scripts/run_mcp.py
```

The launcher execs `python -m productflow_backend.mcp_server` using the backend's `uv` environment. See `scripts/run_mcp.py` for the exact command and example prompts.

### Codex CLI / Claude Desktop config

Add an MCP server entry pointing at the launcher (adjust the absolute path):

```json
{
  "mcpServers": {
    "seftflow": {
      "command": "python",
      "args": ["/abs/path/to/SeftFlow/skills/seftflow/scripts/run_mcp.py"]
    }
  }
}
```

## Tools exposed

| Tool | Purpose |
| --- | --- |
| `list_products` | List products (paginated). |
| `get_workflow_status` | Copy sets + poster variants for a product. |
| `create_product` | Create a text-only product record. |
| `generate_copy` | Return the latest copy set (regeneration runs through the workflow engine). |
| `generate_image` | Submit an image-generation request (creates a session if needed). |
| `add_to_gallery` | Save a generated asset to the gallery. |
| `run_product_workflow` | Return current workflow status for a product. |

Read-only resources: `productflow://products`, `productflow://gallery`.

## Example prompt

> Create a new product "Summer T-shirt", write casual English copy, render a 1024x1024 hero image, then save the best result to the gallery.

The agent should call `create_product`, `generate_copy`, `generate_image`, then `add_to_gallery`, surfacing each tool call for transparency.

## Safety

Every tool runs against the same application use cases as the web app. Destructive actions stay gated by the backend `deletion_enabled` flag, and tool arguments pass a prompt-injection guard (`agent/guards.py`). Never paste live API keys into prompts.