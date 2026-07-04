from __future__ import annotations

import asyncio
import json
from pathlib import Path

from productflow_backend.agent.tools import SeftFlowTools, build_tool_map
from productflow_backend.mcp_server import server


def test_tool_schemas_match_tool_map(configured_env: Path) -> None:
    tool_map = build_tool_map(SeftFlowTools(session_id="mcp-test"))
    assert set(server.TOOL_SCHEMAS) == set(tool_map)


def test_every_schema_has_input_schema() -> None:
    for name, schema in server.TOOL_SCHEMAS.items():
        assert schema["description"], name
        assert schema["inputSchema"]["type"] == "object", name


def test_resources_declared() -> None:
    uris = {resource["uri"] for resource in server.RESOURCES}
    assert uris == {"productflow://products", "productflow://gallery"}


def test_tool_round_trip_through_map(configured_env: Path) -> None:
    """Mirror what the MCP call_tool handler does: resolve + invoke a tool."""
    tools = SeftFlowTools(session_id="mcp:create_product")
    tool_map = build_tool_map(tools)
    result = asyncio.run(asyncio.to_thread(tool_map["create_product"], name="MCP Widget"))
    payload = json.loads(json.dumps(result, default=str))
    assert payload["created"] is True
    assert payload["product"]["name"] == "MCP Widget"

    listed = build_tool_map(SeftFlowTools(session_id="mcp:list_products"))["list_products"]()
    assert listed["total"] == 1