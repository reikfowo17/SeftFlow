from __future__ import annotations

from pathlib import Path

import pytest

from productflow_backend.agent.guards import GuardError
from productflow_backend.agent.tools import SeftFlowTools, build_tool_map


def _tools() -> SeftFlowTools:
    return SeftFlowTools(session_id="test-agent-tools")


def test_build_tool_map_exposes_seven_tools(configured_env: Path) -> None:
    tool_map = build_tool_map(_tools())
    assert set(tool_map) == {
        "list_products",
        "get_workflow_status",
        "create_product",
        "generate_copy",
        "generate_image",
        "add_to_gallery",
        "run_product_workflow",
    }


def test_list_products_starts_empty(configured_env: Path) -> None:
    result = _tools().list_products()
    assert result["total"] == 0
    assert result["items"] == []


def test_create_then_list_product(configured_env: Path) -> None:
    tools = _tools()
    created = tools.create_product(name="Summer Tee", category="Apparel", price="19.99")
    assert created["created"] is True
    assert created["product"]["name"] == "Summer Tee"

    listed = tools.list_products()
    assert listed["total"] == 1
    assert listed["items"][0]["name"] == "Summer Tee"


def test_create_product_rejects_empty_name(configured_env: Path) -> None:
    with pytest.raises(GuardError):
        _tools().create_product(name="   ")


def test_create_product_blocks_prompt_injection(configured_env: Path) -> None:
    with pytest.raises(GuardError):
        _tools().create_product(name="DROP TABLE products")


def test_get_workflow_status_returns_history_shape(configured_env: Path) -> None:
    tools = _tools()
    created = tools.create_product(name="Winter Hoodie")
    product_id = created["product"]["id"]
    status = tools.get_workflow_status(product_id)
    assert status["product_id"] == product_id
    assert status["copy_sets"] == []
    assert status["poster_variants"] == []


def test_tool_call_budget_caps_at_four(configured_env: Path) -> None:
    tools = _tools()
    # Each list_products call spends one tool-call from the per-turn budget.
    for _ in range(4):
        tools.list_products()
    with pytest.raises(GuardError):
        tools.list_products()