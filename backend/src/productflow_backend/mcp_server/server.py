"""SeftFlow MCP server (stdio).

Re-uses the same function tools as the agent so external MCP clients
(Claude Desktop, Codex CLI, Cursor) can drive SeftFlow through the
same interface as the in-app copilot.

Run with: `python -m productflow_backend.mcp_server`
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from productflow_backend.agent.tools import SeftFlowTools, build_tool_map

logger = logging.getLogger("productflow.mcp")


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "list_products": {
        "description": "List SeftFlow products (paginated).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "page": {"type": "integer", "default": 1},
            },
        },
    },
    "get_workflow_status": {
        "description": "Get product history (copy sets, poster variants).",
        "inputSchema": {
            "type": "object",
            "properties": {"product_id": {"type": "string"}},
            "required": ["product_id"],
        },
    },
    "create_product": {
        "description": "Create a text-only product record.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "category": {"type": "string"},
                "price": {"type": "string"},
                "source_note": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    "generate_copy": {
        "description": "Return the latest copy set for a product (regeneration via workflow).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "product_id": {"type": "string"},
                "style": {"type": "string"},
                "instruction": {"type": "string"},
            },
            "required": ["product_id"],
        },
    },
    "generate_image": {
        "description": "Submit an image-generation request (creates a session if needed).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "product_id": {"type": "string"},
                "size": {"type": "string", "default": "1024x1024"},
                "n": {"type": "integer", "default": 1},
                "image_session_id": {"type": "string"},
            },
            "required": ["prompt"],
        },
    },
    "add_to_gallery": {
        "description": "Save a generated image-session asset to the gallery.",
        "inputSchema": {
            "type": "object",
            "properties": {"image_session_asset_id": {"type": "string"}},
            "required": ["image_session_asset_id"],
        },
    },
    "run_product_workflow": {
        "description": "Return current workflow status for a product.",
        "inputSchema": {
            "type": "object",
            "properties": {"product_id": {"type": "string"}},
            "required": ["product_id"],
        },
    },
}


RESOURCES: list[dict[str, Any]] = [
    {
        "uri": "productflow://products",
        "name": "SeftFlow products",
        "mimeType": "application/json",
        "description": "Read-only list of products",
    },
    {
        "uri": "productflow://gallery",
        "name": "SeftFlow gallery",
        "mimeType": "application/json",
        "description": "Read-only list of gallery entries",
    },
]


async def _run_mcp_server() -> None:
    try:
        from mcp.server import Server  # type: ignore
        from mcp.server.stdio import stdio_server  # type: ignore
        from mcp.types import Resource, TextContent, Tool  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise SystemExit(
            "mcp SDK is not installed. Install with `uv add mcp` in backend/."
        ) from exc

    server = Server("productflow")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(name=name, description=schema["description"], inputSchema=schema["inputSchema"])
            for name, schema in TOOL_SCHEMAS.items()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        tools = SeftFlowTools(session_id=f"mcp:{name}")
        tool_map = build_tool_map(tools)
        if name not in tool_map:
            raise ValueError(f"Unknown tool: {name}")
        result = await asyncio.to_thread(tool_map[name], **(arguments or {}))
        return [TextContent(type="text", text=json.dumps(result, default=str))]

    @server.list_resources()
    async def list_resources() -> list[Resource]:
        return [Resource(**item) for item in RESOURCES]

    @server.read_resource()
    async def read_resource(uri: str) -> str:
        tools = SeftFlowTools(session_id=f"mcp:resource:{uri}")
        tool_map = build_tool_map(tools)
        if uri == "productflow://products":
            return json.dumps(await asyncio.to_thread(tool_map["list_products"]))
        if uri == "productflow://gallery":
            # Gallery listing bypasses agent tools; go straight to the use case.
            from productflow_backend.application import gallery as gallery_uc
            from productflow_backend.infrastructure.db.session import get_session_factory

            factory = get_session_factory()
            with factory() as db:
                entries = gallery_uc.list_gallery_entries(db)
                return json.dumps(
                    [{"id": e.id, "created_at": e.created_at.isoformat()} for e in entries],
                    default=str,
                )
        raise ValueError(f"Unknown resource: {uri}")

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run_mcp_server())


if __name__ == "__main__":
    main()
