from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from helpers import (
    _execute_workflow_queue_inline,
    _login,
    _make_demo_image_bytes,
    _wait_for_workflow_run,
)

from productflow_backend.application.canvas_templates import (
    get_builtin_canvas_template,
    list_builtin_canvas_templates,
)
from productflow_backend.application.contracts import (
    BlocksCopyContent,
    CopyBlock,
    CopyNodeConfigV2,
    CopyPayloadV2,
    CreativeBriefPayload,
    PosterGenerationInput,
    ProductInput,
)
from productflow_backend.application.product_workflow.templates import TEMPLATE_METADATA_CONFIG_KEY
from productflow_backend.application.product_workflow_dependencies import WorkflowExecutionDependencies
from productflow_backend.domain.enums import (
    CopyStatus,
    PosterKind,
    WorkflowNodeType,
)
from productflow_backend.infrastructure.db.models import (
    AppSetting,
    CopySet,
    PosterVariant,
    Product,
    ProductWorkflow,
    ProviderBinding,
    ProviderProfile,
    WorkflowEdge,
    WorkflowNode,
)
from productflow_backend.infrastructure.db.session import get_session_factory

_WORKFLOW_NODE_VISUAL_WIDTH = 248
_WORKFLOW_NODE_VISUAL_HEIGHT = 248


def _template_config(template_key: str, node_key: str, config_json: dict) -> dict:
    return {
        **config_json,
        TEMPLATE_METADATA_CONFIG_KEY: {
            "source": "builtin",
            "template_key": template_key,
            "node_key": node_key,
        },
    }
_WORKFLOW_TEMPLATE_CONTEXT_ANCHOR_GAP = 220
REMOVED_COPY_OUTPUT_KEYS = [
    "derived" + "_fields",
    "title",
    "selling" + "_points",
    "poster" + "_headline",
    "c" + "ta",
]


@pytest.fixture(autouse=True)
def _execute_workflow_queue_inline_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep API workflow tests deterministic while production delivery goes through Dramatiq."""

    _execute_workflow_queue_inline(monkeypatch)


def test_product_workflow_dag_runs_and_persists_artifacts(configured_env: Path) -> None:
    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post(
        "/api/products",
        data={"name": "multi-purpose storage rack"},
        files={"image": ("rack.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]

    workflow_response = client.get(f"/api/products/{product_id}/workflow")
    assert workflow_response.status_code == 200
    workflow = workflow_response.json()
    assert len(workflow["nodes"]) >= 4
    assert len(workflow["edges"]) >= 3
    assert {node["node_type"] for node in workflow["nodes"]} == {
        "product_context",
        "reference_image",
        "copy_generation",
        "image_generation",
    }

    context_node = next(node for node in workflow["nodes"] if node["node_type"] == "product_context")
    updated_context = client.patch(
        f"/api/workflow-nodes/{context_node['id']}",
        json={
            "position_x": 96,
            "position_y": 144,
            "config_json": {
                "name": "multi-purpose storage rack",
                "category": "home goods",
                "price": "49.90",
                "source_note": "drill-free installation，suits kitchen and bathroom，emphasizes load capacity and tidiness。",
            }
        },
    )
    assert updated_context.status_code == 200
    moved_context = next(node for node in updated_context.json()["nodes"] if node["id"] == context_node["id"])
    assert moved_context["position_x"] == 96
    assert moved_context["position_y"] == 144

    manual_reference = client.post(
        f"/api/products/{product_id}/workflow/nodes",
        json={
            "node_type": "reference_image",
            "title": "style reference",
            "position_x": 320,
            "position_y": 260,
            "config_json": {"role": "style", "label": "kitchen style"},
        },
    )
    assert manual_reference.status_code == 201
    upload_node = next(
        node
        for node in manual_reference.json()["nodes"]
        if node["node_type"] == "reference_image" and node["title"] == "style reference"
    )
    uploaded = client.post(
        f"/api/workflow-nodes/{upload_node['id']}/image",
        data={"role": "style", "label": "kitchen style image"},
        files={"image": ("style.png", _make_demo_image_bytes(), "image/png")},
    )
    assert uploaded.status_code == 200
    uploaded_node = next(node for node in uploaded.json()["nodes"] if node["id"] == upload_node["id"])
    assert uploaded_node["output_json"]["source_asset_ids"]

    copy_node = next(node for node in workflow["nodes"] if node["node_type"] == "copy_generation")
    updated = client.patch(
        f"/api/workflow-nodes/{copy_node['id']}",
        json={"config_json": {"instruction": "emphasizedrill-freewith tidy kitchen scene"}},
    )
    assert updated.status_code == 200
    reference_to_copy = client.post(
        f"/api/products/{product_id}/workflow/edges",
        json={
            "source_node_id": upload_node["id"],
            "target_node_id": copy_node["id"],
            "source_handle": "output",
            "target_handle": "input",
        },
    )
    assert reference_to_copy.status_code == 201
    image_node = next(node for node in workflow["nodes"] if node["node_type"] == "image_generation")
    updated_image = client.patch(
        f"/api/workflow-nodes/{image_node['id']}",
        json={"config_json": {"instruction": "follow upstreamcopyandreference image，generatemain image", "size": "1024x1024"}},
    )
    assert updated_image.status_code == 200

    upstream_edge = client.post(
        f"/api/products/{product_id}/workflow/edges",
        json={
            "source_node_id": upload_node["id"],
            "target_node_id": image_node["id"],
            "source_handle": "output",
            "target_handle": "input",
        },
    )
    assert upstream_edge.status_code == 201
    default_reference_node = next(
        node
        for node in workflow["nodes"]
        if node["node_type"] == "reference_image" and node["title"] == "Reference image"
    )
    default_target_edge = client.post(
        f"/api/products/{product_id}/workflow/edges",
        json={
            "source_node_id": image_node["id"],
            "target_node_id": default_reference_node["id"],
            "source_handle": "output",
            "target_handle": "input",
        },
    )
    assert default_target_edge.status_code == 201
    second_target = client.post(
        f"/api/products/{product_id}/workflow/nodes",
        json={
            "node_type": "reference_image",
            "title": "reference image 2",
            "position_x": 1160,
            "position_y": 240,
            "config_json": {"role": "reference", "label": "reference image 2"},
        },
    )
    assert second_target.status_code == 201
    second_target_node = next(node for node in second_target.json()["nodes"] if node["title"] == "reference image 2")
    second_target_edge = client.post(
        f"/api/products/{product_id}/workflow/edges",
        json={
            "source_node_id": image_node["id"],
            "target_node_id": second_target_node["id"],
            "source_handle": "output",
            "target_handle": "input",
        },
    )
    assert second_target_edge.status_code == 201
    duplicate_target_edge = client.post(
        f"/api/products/{product_id}/workflow/edges",
        json={
            "source_node_id": image_node["id"],
            "target_node_id": second_target_node["id"],
            "source_handle": "output",
            "target_handle": "input",
        },
    )
    assert duplicate_target_edge.status_code == 201
    workflow_before_run = duplicate_target_edge.json()

    run_response = client.post(f"/api/products/{product_id}/workflow/run", json={})
    assert run_response.status_code == 200
    assert run_response.json()["runs"][0]["status"] == "running"
    run_payload = _wait_for_workflow_run(client, product_id, status="succeeded")
    assert run_payload["runs"][0]["status"] == "succeeded"
    assert all(node["status"] == "succeeded" for node in run_payload["nodes"])
    copy_output = next(node for node in run_payload["nodes"] if node["node_type"] == "copy_generation")["output_json"]
    assert copy_output["copy_set_id"]
    assert copy_output["structured_payload"]["version"] == 2
    assert not set(REMOVED_COPY_OUTPUT_KEYS) & set(copy_output)
    assert "drill-free" in str(copy_output["structured_payload"])
    assert "kitchen style image" in str(copy_output["structured_payload"])
    edited_copy = client.patch(
        f"/api/workflow-nodes/{copy_node['id']}/copy",
        json={
            "structured_payload": {
                "version": 2,
                "summary": "kitchen tidy in one step",
                "content": {
                    "kind": "blocks",
                    "blocks": [
                        {"id": "headline", "text": "kitchendrill-freestorage rack"},
                        {"id": "point-1", "text": "drill-free installation"},
                        {"id": "point-2", "text": "kitchen counter looks tidier"},
                        {"id": "point-3", "text": "loadsteadyfixed"},
                    ],
                },
            },
        },
    )
    assert edited_copy.status_code == 200
    edited_copy_node = next(node for node in edited_copy.json()["nodes"] if node["id"] == copy_node["id"])
    assert edited_copy_node["output_json"]["structured_payload"]["version"] == 2
    assert not set(REMOVED_COPY_OUTPUT_KEYS) & set(edited_copy_node["output_json"])
    rerun_image = client.post(
        f"/api/products/{product_id}/workflow/run",
        json={"start_node_id": image_node["id"]},
    )
    assert rerun_image.status_code == 200
    rerun_payload = _wait_for_workflow_run(client, product_id, status="succeeded")
    assert rerun_payload["runs"][0]["status"] == "succeeded"
    rerun_copy_output = next(node for node in rerun_payload["nodes"] if node["id"] == copy_node["id"])["output_json"]
    rerun_image_output = next(node for node in rerun_payload["nodes"] if node["id"] == image_node["id"])["output_json"]
    assert rerun_copy_output["copy_set_id"] == copy_output["copy_set_id"]
    assert not set(REMOVED_COPY_OUTPUT_KEYS) & set(rerun_copy_output)
    assert rerun_image_output["copy_set_id"] == copy_output["copy_set_id"]
    image_output = next(node for node in run_payload["nodes"] if node["node_type"] == "image_generation")["output_json"]
    assert "poster_variant_ids" not in image_output
    assert len(image_output["generated_poster_variant_ids"]) == 2
    assert image_output["target_count"] == 2
    assert len(image_output["filled_source_asset_ids"]) == 2
    assert len(image_output["filled_reference_node_ids"]) == 2
    assert image_output["size"] == "1024x1024"
    context_sources = image_output["context_sources"]
    assert any(source["label"] == "copy" and "multi-purpose storage rack" in source["text"] for source in context_sources)
    assert any(source["label"] == "reference image" and "kitchen style image" in source["text"] for source in context_sources)
    assert image_output["context_summary"]["reference_image_count"] >= 1
    filled_nodes = [
        node for node in run_payload["nodes"] if node["id"] in set(image_output["filled_reference_node_ids"])
    ]
    assert all(node["output_json"]["source_asset_ids"] for node in filled_nodes)

    product_after = client.get(f"/api/products/{product_id}")
    assert product_after.status_code == 200
    product_payload = product_after.json()
    assert any(copy_set["id"] == copy_output["copy_set_id"] for copy_set in product_payload["copy_sets"])
    assert len(product_payload["poster_variants"]) == 4
    reference_assets = [asset for asset in product_payload["source_assets"] if asset["kind"] == "reference_image"]
    assert len(reference_assets) == 5

    rejected_cycle = client.post(
        f"/api/products/{product_id}/workflow/edges",
        json={
            "source_node_id": image_node["id"],
            "target_node_id": copy_node["id"],
            "source_handle": "output",
            "target_handle": "input",
        },
    )
    assert rejected_cycle.status_code == 400
    assert "must not contain cyclic dependencies" in rejected_cycle.json()["detail"]
    refreshed = client.get(f"/api/products/{product_id}/workflow")
    assert refreshed.status_code == 200
    assert len(refreshed.json()["edges"]) == len(workflow_before_run["edges"])

    edge_to_delete = refreshed.json()["edges"][0]
    deleted_edge = client.delete(f"/api/workflow-edges/{edge_to_delete['id']}")
    assert deleted_edge.status_code == 200
    deleted_payload = deleted_edge.json()
    assert len(deleted_payload["edges"]) == len(workflow_before_run["edges"]) - 1
    assert edge_to_delete["id"] not in {edge["id"] for edge in deleted_payload["edges"]}

    isolated_image = client.post(
        f"/api/products/{product_id}/workflow/nodes",
        json={
            "node_type": "image_generation",
            "title": "image generation not connected",
            "position_x": 620,
            "position_y": 420,
            "config_json": {"instruction": "generate but do not save slot", "size": "1024x1024"},
        },
    )
    assert isolated_image.status_code == 201
    isolated_image_node = next(node for node in isolated_image.json()["nodes"] if node["title"] == "image generation not connected")
    context_to_isolated = client.post(
        f"/api/products/{product_id}/workflow/edges",
        json={
            "source_node_id": context_node["id"],
            "target_node_id": isolated_image_node["id"],
            "source_handle": "output",
            "target_handle": "input",
        },
    )
    assert context_to_isolated.status_code == 201
    copy_to_isolated = client.post(
        f"/api/products/{product_id}/workflow/edges",
        json={
            "source_node_id": copy_node["id"],
            "target_node_id": isolated_image_node["id"],
            "source_handle": "output",
            "target_handle": "input",
        },
    )
    assert copy_to_isolated.status_code == 201
    direct_run = client.post(
        f"/api/products/{product_id}/workflow/run",
        json={"start_node_id": isolated_image_node["id"]},
    )
    assert direct_run.status_code == 200
    direct_payload = _wait_for_workflow_run(client, product_id, status="failed")
    assert direct_payload["runs"][0]["status"] == "failed"
    assert "at least one image or reference image node" in direct_payload["runs"][0]["failure_reason"]
    direct_node = next(node for node in direct_payload["nodes"] if node["id"] == isolated_image_node["id"])
    assert direct_node["status"] == "failed"
    assert "at least one image or reference image node" in direct_node["failure_reason"]

    session = get_session_factory()()
    try:
        assert session.query(ProductWorkflow).filter_by(product_id=product_id).count() == 1
    finally:
        session.close()


def test_real_image_binding_uses_provider_even_when_legacy_poster_mode_is_template(
    configured_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.infrastructure.image.base import GeneratedImagePayload
    from productflow_backend.presentation.api import create_app

    session = get_session_factory()()
    try:
        profile = ProviderProfile(
            name="realimageprovider",
            provider_type="openai_compatible",
            base_url="https://image.example/v1",
            api_key="image-secret-key",
            capabilities_json=["image_images"],
            default_models_json={"image_model": "gpt-image-2"},
            config_json={},
            enabled=True,
        )
        session.add(profile)
        session.flush()
        session.add(
            ProviderBinding(
                purpose="image",
                provider_kind="openai_images",
                provider_profile_id=profile.id,
                model_settings_json={"model": "gpt-image-2"},
                config_json={"images_quality": "high", "images_style": "natural"},
            )
        )
        session.add(AppSetting(key="poster_generation_mode", value="template"))
        session.commit()
    finally:
        session.close()

    captured_inputs: list[PosterGenerationInput] = []

    class CapturingImageProvider:
        provider_name = "capturing"
        prompt_version = "capturing-v1"

        def generate_poster_image(
            self,
            poster: PosterGenerationInput,
            kind: PosterKind,
        ) -> tuple[GeneratedImagePayload, str]:
            captured_inputs.append(poster)
            return (
                GeneratedImagePayload(
                    kind=kind,
                    bytes_data=_make_demo_image_bytes(),
                    mime_type="image/png",
                    width=800,
                    height=800,
                    variant_label="real-binding",
                    provider_response_id="resp-real-binding",
                    provider_response_status="completed",
                ),
                "gpt-image-2",
            )

    def fail_renderer(font_path: Path) -> object:
        raise AssertionError(f"template renderer should not be used for real image bindings: {font_path}")

    _execute_workflow_queue_inline(
        monkeypatch,
        dependencies=WorkflowExecutionDependencies(
            image_provider_resolver=CapturingImageProvider,
            poster_renderer_factory=fail_renderer,
        ),
    )

    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post(
        "/api/products",
        data={"name": "real provider override template mode"},
        files={"image": ("real-binding.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]

    workflow = client.get(f"/api/products/{product_id}/workflow").json()
    copy_node = next(node for node in workflow["nodes"] if node["node_type"] == "copy_generation")
    image_node = next(node for node in workflow["nodes"] if node["node_type"] == "image_generation")
    deleted_copy = client.delete(f"/api/workflow-nodes/{copy_node['id']}")
    assert deleted_copy.status_code == 200

    run = client.post(f"/api/products/{product_id}/workflow/run", json={"start_node_id": image_node["id"]})
    assert run.status_code == 200
    payload = _wait_for_workflow_run(client, product_id, status="succeeded")
    image_output = next(node for node in payload["nodes"] if node["id"] == image_node["id"])["output_json"]

    assert len(captured_inputs) == 1
    assert image_output["provider_results"] == [
        {
            "target_index": 1,
            "provider_name": "capturing",
            "model_name": "gpt-image-2",
            "provider_response_id": "resp-real-binding",
            "provider_response_status": "completed",
        }
    ]

    session = get_session_factory()()
    try:
        posters = list(session.scalars(sa.select(PosterVariant).where(PosterVariant.product_id == product_id)).all())
    finally:
        session.close()
    assert len(posters) == 1
    assert posters[0].template_name == "workflow:capturing:real-binding:gpt-image-2"


def test_canvas_template_catalog_endpoint_lists_builtin_scenario_templates(configured_env: Path) -> None:
    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)
    _login(client)

    response = client.get("/api/workflow/canvas-templates")

    assert response.status_code == 200
    payload = response.json()
    assert {item["key"] for item in payload["items"]} == {template.key for template in list_builtin_canvas_templates()}
    scenario_template = next(item for item in payload["items"] if item["key"] == "ecommerce-sku-variant-image-v1")
    template = get_builtin_canvas_template("ecommerce-sku-variant-image-v1")
    assert scenario_template["kind"] == "full_canvas"
    assert scenario_template["preview_nodes"] == [
        {
            "key": node.key,
            "node_type": node.node_type.value,
            "title": node.title,
            "position_x": node.position_x,
            "position_y": node.position_y,
            "size": node.size,
        }
        for node in template.nodes
    ]
    assert scenario_template["preview_edges"] == [
        {
            "source_node_key": edge.source_node_key,
            "target_node_key": edge.target_node_key,
        }
        for edge in template.edges
    ]
    assert all("config_json" not in node for node in scenario_template["preview_nodes"])
    assert scenario_template["reference_input_hints"]
    assert scenario_template["output_slots"]
    assert scenario_template["suggested_connections"]
    assert scenario_template["default_external_connections"] == []
    assert all("config_json" not in connection for connection in scenario_template["default_external_connections"])
    assert all("reason" not in connection for connection in scenario_template["default_external_connections"])


def test_apply_builtin_scenario_template_appends_real_workflow_nodes_and_edges(
    configured_env: Path,
    db_session,
) -> None:
    from productflow_backend.presentation.api import create_app

    template = get_builtin_canvas_template("ecommerce-sku-variant-image-v1")
    app = create_app()
    client = TestClient(app)
    _login(client)
    created = client.post(
        "/api/products",
        data={"name": "scene template append product"},
        files={"image": ("scenario-template.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]
    original = client.get(f"/api/products/{product_id}/workflow").json()
    original_node_ids = {node["id"] for node in original["nodes"]}
    original_edges = {
        (edge["source_node_id"], edge["target_node_id"], edge["source_handle"], edge["target_handle"])
        for edge in original["edges"]
    }

    response = client.post(
        f"/api/products/{product_id}/workflow/template-groups",
        json={"template_key": template.key, "position_x": 480, "position_y": 360},
    )

    assert response.status_code == 201
    workflow = response.json()
    insertable_template_nodes = [
        node for node in template.nodes if node.node_type != WorkflowNodeType.PRODUCT_CONTEXT
    ]
    assert len(workflow["nodes"]) == len(original["nodes"]) + len(insertable_template_nodes)
    assert len(workflow["edges"]) == len(original["edges"]) + len(template.edges)
    assert len(
        [node for node in workflow["nodes"] if node["node_type"] == WorkflowNodeType.PRODUCT_CONTEXT.value]
    ) == 1
    product_context_node = next(node for node in original["nodes"] if node["node_type"] == "product_context")
    assert original_node_ids <= {node["id"] for node in workflow["nodes"]}
    assert original_edges <= {
        (edge["source_node_id"], edge["target_node_id"], edge["source_handle"], edge["target_handle"])
        for edge in workflow["edges"]
    }

    created_nodes = [node for node in workflow["nodes"] if node["id"] not in original_node_ids]
    created_node_ids = {node["id"] for node in created_nodes}
    assert {node["node_type"] for node in created_nodes} == {
        "reference_image",
        "copy_generation",
        "image_generation",
    }
    min_x = min(node.position_x for node in insertable_template_nodes)
    min_y = min(node.position_y for node in insertable_template_nodes)
    expected_min_x = max(
        480,
        product_context_node["position_x"] + _WORKFLOW_NODE_VISUAL_WIDTH + _WORKFLOW_TEMPLATE_CONTEXT_ANCHOR_GAP,
    )
    expected_min_y = 360
    unmatched_nodes = list(created_nodes)
    persisted_node_ids_by_template_key: dict[str, str] = {"product": product_context_node["id"]}
    for template_node in insertable_template_nodes:
        matched_node = next(
            (
                node
                for node in unmatched_nodes
                if node["node_type"] == template_node.node_type.value
                and node["title"] == template_node.title
                and node["position_x"] == template_node.position_x - min_x + expected_min_x
                and node["position_y"] == template_node.position_y - min_y + expected_min_y
                and node["config_json"] == _template_config(template.key, template_node.key, template_node.config_json)
            ),
            None,
        )
        assert matched_node is not None
        unmatched_nodes.remove(matched_node)
        persisted_node_ids_by_template_key[template_node.key] = matched_node["id"]

    created_edges = [
        edge
        for edge in workflow["edges"]
        if edge["source_node_id"] in created_node_ids or edge["target_node_id"] in created_node_ids
    ]
    assert len(created_edges) == len(template.edges)
    assert all(edge["source_node_id"] != edge["target_node_id"] for edge in created_edges)
    assert {
        (edge["source_node_id"], edge["target_node_id"], edge["source_handle"], edge["target_handle"])
        for edge in created_edges
    } == {
        (
            persisted_node_ids_by_template_key[edge.source_node_key],
            persisted_node_ids_by_template_key[edge.target_node_key],
            edge.source_handle,
            edge.target_handle,
        )
        for edge in template.edges
    }
    assert any(
        edge["source_node_id"] == product_context_node["id"] and edge["target_node_id"] in created_node_ids
        for edge in created_edges
    )
    db_session.expire_all()
    workflow_row = db_session.query(ProductWorkflow).filter_by(product_id=product_id, active=True).one()
    assert db_session.query(WorkflowNode).filter_by(workflow_id=workflow_row.id).count() == len(workflow["nodes"])
    assert db_session.query(WorkflowEdge).filter_by(workflow_id=workflow_row.id).count() == len(workflow["edges"])


def test_apply_full_canvas_template_reuses_existing_product_context_node(
    configured_env: Path,
    db_session,
) -> None:
    from productflow_backend.presentation.api import create_app

    template = get_builtin_canvas_template("ecommerce-taobao-main-image-v1")
    app = create_app()
    client = TestClient(app)
    _login(client)
    created = client.post(
        "/api/products",
        data={"name": "reuseproductnodeproduct"},
        files={"image": ("reuse-context.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]
    original = client.get(f"/api/products/{product_id}/workflow").json()
    original_node_ids = {node["id"] for node in original["nodes"]}
    product_context_node = next(node for node in original["nodes"] if node["node_type"] == "product_context")

    response = client.post(
        f"/api/products/{product_id}/workflow/template-groups",
        json={"template_key": template.key, "position_x": 720, "position_y": 360},
    )

    assert response.status_code == 201
    workflow = response.json()
    created_nodes = [node for node in workflow["nodes"] if node["id"] not in original_node_ids]
    assert len(created_nodes) == len(
        [node for node in template.nodes if node.node_type != WorkflowNodeType.PRODUCT_CONTEXT]
    )
    assert len([node for node in workflow["nodes"] if node["node_type"] == "product_context"]) == 1
    assert all(node["id"] != product_context_node["id"] for node in created_nodes)

    min_x = min(node.position_x for node in template.nodes if node.node_type != WorkflowNodeType.PRODUCT_CONTEXT)
    min_y = min(node.position_y for node in template.nodes if node.node_type != WorkflowNodeType.PRODUCT_CONTEXT)
    min_created_x = min(node["position_x"] for node in created_nodes)
    min_created_y = min(node["position_y"] for node in created_nodes)
    unmatched_nodes = list(created_nodes)
    persisted_node_ids_by_template_key = {"product": product_context_node["id"]}
    for template_node in template.nodes:
        if template_node.node_type == WorkflowNodeType.PRODUCT_CONTEXT:
            continue
        matched_node = next(
            (
                node
                for node in unmatched_nodes
                if node["node_type"] == template_node.node_type.value
                and node["title"] == template_node.title
                and node["position_x"] == template_node.position_x - min_x + min_created_x
                and node["position_y"] == template_node.position_y - min_y + min_created_y
                and node["config_json"] == _template_config(template.key, template_node.key, template_node.config_json)
            ),
            None,
        )
        assert matched_node is not None
        unmatched_nodes.remove(matched_node)
        persisted_node_ids_by_template_key[template_node.key] = matched_node["id"]

    created_node_ids = {node["id"] for node in created_nodes}
    created_edges = [
        edge
        for edge in workflow["edges"]
        if edge["source_node_id"] in created_node_ids or edge["target_node_id"] in created_node_ids
    ]
    assert {
        (edge["source_node_id"], edge["target_node_id"], edge["source_handle"], edge["target_handle"])
        for edge in created_edges
    } == {
        (
            persisted_node_ids_by_template_key[edge.source_node_key],
            persisted_node_ids_by_template_key[edge.target_node_key],
            edge.source_handle,
            edge.target_handle,
        )
        for edge in template.edges
    }
    assert any(edge["source_node_id"] == product_context_node["id"] for edge in created_edges)

    db_session.expire_all()
    workflow_row = db_session.query(ProductWorkflow).filter_by(product_id=product_id, active=True).one()
    assert db_session.query(WorkflowNode).filter_by(
        workflow_id=workflow_row.id,
        node_type=WorkflowNodeType.PRODUCT_CONTEXT,
    ).count() == 1


@pytest.mark.parametrize(
    "template_key",
    [
        "ecommerce-sku-variant-image-v1",
        "ecommerce-detail-material-image-v1",
        "ecommerce-white-background-image-v1",
    ],
)
def test_builtin_scenario_template_runs_with_auto_product_context_edges(
    configured_env: Path,
    monkeypatch: pytest.MonkeyPatch,
    template_key: str,
) -> None:
    from productflow_backend.infrastructure.image.base import GeneratedImagePayload
    from productflow_backend.presentation.api import create_app

    session = get_session_factory()()
    try:
        session.add(AppSetting(key="poster_generation_mode", value="generated"))
        session.commit()
    finally:
        session.close()

    captured_inputs: list[PosterGenerationInput] = []

    class CapturingImageProvider:
        provider_name = "capturing"
        prompt_version = "capturing-v1"

        def generate_poster_image(
            self,
            poster: PosterGenerationInput,
            kind: PosterKind,
        ) -> tuple[GeneratedImagePayload, str]:
            captured_inputs.append(poster)
            return (
                GeneratedImagePayload(
                    kind=kind,
                    bytes_data=_make_demo_image_bytes(),
                    mime_type="image/png",
                    width=800,
                    height=800,
                    variant_label=f"capturing-{template_key}",
                ),
                "capturing-v1",
            )

    _execute_workflow_queue_inline(
        monkeypatch,
        dependencies=WorkflowExecutionDependencies(
            image_provider_resolver=CapturingImageProvider,
        ),
    )

    template = get_builtin_canvas_template(template_key)
    app = create_app()
    client = TestClient(app)
    _login(client)
    created = client.post(
        "/api/products",
        data={"name": "auto-attach test product"},
        files={"image": ("auto-context.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]
    workflow = client.get(f"/api/products/{product_id}/workflow").json()
    product_context_node = next(node for node in workflow["nodes"] if node["node_type"] == "product_context")
    patched_context = client.patch(
        f"/api/workflow-nodes/{product_context_node['id']}",
        json={
            "config_json": {
                "name": "auto-attach test product",
                "category": "image tool",
                "price": "199",
                "source_note": "verifies that scene templates inherit product info and main image。",
            }
        },
    )
    assert patched_context.status_code == 200
    original_node_ids = {node["id"] for node in patched_context.json()["nodes"]}

    applied = client.post(
        f"/api/products/{product_id}/workflow/template-groups",
        json={"template_key": template.key, "position_x": 720, "position_y": 420},
    )

    assert applied.status_code == 201
    applied_workflow = applied.json()
    created_nodes = [node for node in applied_workflow["nodes"] if node["id"] not in original_node_ids]
    template_copy_node = next(node for node in created_nodes if node["node_type"] == "copy_generation")
    template_image_node = next(node for node in created_nodes if node["node_type"] == "image_generation")
    template_output_node = next(
        node
        for node in created_nodes
        if node["node_type"] == "reference_image" and node["title"] == template.output_slots[0].label
    )
    assert any(
        edge["source_node_id"] == product_context_node["id"]
        and edge["target_node_id"] == template_copy_node["id"]
        for edge in applied_workflow["edges"]
    )
    assert any(
        edge["source_node_id"] == product_context_node["id"]
        and edge["target_node_id"] == template_image_node["id"]
        for edge in applied_workflow["edges"]
    )

    run = client.post(
        f"/api/products/{product_id}/workflow/run",
        json={"start_node_id": template_image_node["id"]},
    )

    assert run.status_code == 200
    payload = _wait_for_workflow_run(client, product_id, status="succeeded")
    ran_image_node = next(node for node in payload["nodes"] if node["id"] == template_image_node["id"])
    filled_output_node = next(node for node in payload["nodes"] if node["id"] == template_output_node["id"])
    image_output = ran_image_node["output_json"]
    assert image_output["context_summary"]["product_context"] == {
        "name": "auto-attach test product",
        "category": "image tool",
        "price": "199",
        "source_note": "verifies that scene templates inherit product info and main image。",
    }
    assert image_output["context_summary"]["reference_image_count"] == 1
    assert template_output_node["id"] in image_output["filled_reference_node_ids"]
    assert filled_output_node["output_json"]["source_asset_ids"]
    assert len(captured_inputs) == 1
    provider_input = captured_inputs[0]
    assert provider_input.product_name == "auto-attach test product"
    assert provider_input.category == "image tool"
    assert provider_input.price == "199"
    assert provider_input.source_note == "verifies that scene templates inherit product info and main image。"
    assert provider_input.source_image is not None
    assert len(provider_input.reference_images) == 1
    assert provider_input.reference_images[0].path == provider_input.source_image


def test_apply_builtin_scenario_template_avoids_existing_node_overlap(configured_env: Path) -> None:
    from productflow_backend.presentation.api import create_app

    def node_box(node: dict[str, object]) -> tuple[int, int, int, int]:
        left = int(node["position_x"])
        top = int(node["position_y"])
        return (
            left,
            top,
            left + _WORKFLOW_NODE_VISUAL_WIDTH,
            top + _WORKFLOW_NODE_VISUAL_HEIGHT,
        )

    def boxes_overlap(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> bool:
        return first[0] < second[2] and first[2] > second[0] and first[1] < second[3] and first[3] > second[1]

    template = get_builtin_canvas_template("ecommerce-sku-variant-image-v1")
    app = create_app()
    client = TestClient(app)
    _login(client)
    created = client.post(
        "/api/products",
        data={"name": "scene template avoid product"},
        files={"image": ("scenario-overlap.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]
    original = client.get(f"/api/products/{product_id}/workflow").json()
    original_node_ids = {node["id"] for node in original["nodes"]}
    target = original["nodes"][0]

    response = client.post(
        f"/api/products/{product_id}/workflow/template-groups",
        json={"template_key": template.key, "position_x": target["position_x"], "position_y": target["position_y"]},
    )

    assert response.status_code == 201
    workflow = response.json()
    created_nodes = [node for node in workflow["nodes"] if node["id"] not in original_node_ids]
    insertable_template_nodes = [
        node for node in template.nodes if node.node_type != WorkflowNodeType.PRODUCT_CONTEXT
    ]
    assert len(created_nodes) == len(insertable_template_nodes)
    product_context_node = next(node for node in original["nodes"] if node["node_type"] == "product_context")
    assert min(node["position_x"] for node in created_nodes) >= (
        product_context_node["position_x"] + _WORKFLOW_NODE_VISUAL_WIDTH + _WORKFLOW_TEMPLATE_CONTEXT_ANCHOR_GAP
    )
    assert min(node["position_y"] for node in created_nodes) >= product_context_node["position_y"]
    assert not any(
        boxes_overlap(node_box(created_node), node_box(original_node))
        for created_node in created_nodes
        for original_node in original["nodes"]
    )

    min_template_x = min(node.position_x for node in insertable_template_nodes)
    min_template_y = min(node.position_y for node in insertable_template_nodes)
    min_created_x = min(node["position_x"] for node in created_nodes)
    min_created_y = min(node["position_y"] for node in created_nodes)
    unmatched_nodes = list(created_nodes)
    for template_node in insertable_template_nodes:
        matched_node = next(
            (
                node
                for node in unmatched_nodes
                if node["node_type"] == template_node.node_type.value
                and node["title"] == template_node.title
                and node["position_x"] == template_node.position_x - min_template_x + min_created_x
                and node["position_y"] == template_node.position_y - min_template_y + min_created_y
            ),
            None,
        )
        assert matched_node is not None
        unmatched_nodes.remove(matched_node)


def test_apply_template_requires_existing_active_workflow(configured_env: Path) -> None:
    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)
    _login(client)
    created = client.post(
        "/api/products",
        data={"name": "canvas not openedproduct"},
        files={"image": ("no-workflow.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]

    response = client.post(
        f"/api/products/{product_id}/workflow/template-groups",
        json={"template_key": "ecommerce-sku-variant-image-v1", "position_x": 120, "position_y": 120},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Open or create a canvas before adding a template"
    session = get_session_factory()()
    try:
        assert session.query(ProductWorkflow).filter_by(product_id=product_id).count() == 0
        assert session.query(ProductWorkflow).filter_by(product_id=product_id, active=True).count() == 0
    finally:
        session.close()


def test_apply_template_requires_product_context_node(configured_env: Path) -> None:
    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)
    _login(client)
    created = client.post(
        "/api/products",
        data={"name": "missingproductnodeproduct"},
        files={"image": ("missing-context.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]
    workflow = client.get(f"/api/products/{product_id}/workflow").json()
    context_node = next(node for node in workflow["nodes"] if node["node_type"] == "product_context")
    deleted = client.delete(f"/api/workflow-nodes/{context_node['id']}")
    assert deleted.status_code == 200
    without_context = deleted.json()

    response = client.post(
        f"/api/products/{product_id}/workflow/template-groups",
        json={"template_key": "ecommerce-sku-variant-image-v1", "position_x": 120, "position_y": 120},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Template requires a Product info node in the current canvas"
    session = get_session_factory()()
    try:
        workflow_row = session.query(ProductWorkflow).filter_by(product_id=product_id, active=True).one()
        assert session.query(WorkflowNode).filter_by(workflow_id=workflow_row.id).count() == len(
            without_context["nodes"]
        )
        assert session.query(WorkflowEdge).filter_by(workflow_id=workflow_row.id).count() == len(
            without_context["edges"]
        )
    finally:
        session.close()


def test_apply_template_rejects_unknown_key(configured_env: Path) -> None:
    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)
    _login(client)
    created = client.post(
        "/api/products",
        data={"name": "template rejectedproduct"},
        files={"image": ("reject.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]

    missing = client.post(
        f"/api/products/{product_id}/workflow/template-groups",
        json={"template_key": "missing-template", "position_x": 120, "position_y": 120},
    )
    assert missing.status_code == 400
    assert "Canvas template not found" in missing.json()["detail"]


def test_user_template_group_create_list_rename_archive_and_apply(configured_env: Path) -> None:
    from productflow_backend.infrastructure.db.models import UserCanvasTemplate, WorkflowEdge, WorkflowNode
    from productflow_backend.infrastructure.db.session import get_session_factory
    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)
    _login(client)
    created = client.post(
        "/api/products",
        data={"name": "user templateproduct"},
        files={"image": ("user-template.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]
    workflow = client.get(f"/api/products/{product_id}/workflow").json()
    copy_node = next(node for node in workflow["nodes"] if node["node_type"] == "copy_generation")
    image_node = next(node for node in workflow["nodes"] if node["node_type"] == "image_generation")
    output_node = next(node for node in workflow["nodes"] if node["node_type"] == "reference_image")

    saved = client.post(
        f"/api/products/{product_id}/workflow/user-template-groups",
        json={
            "title": "common main image link",
            "description": "reusecopytoimage generationthen save slot",
            "node_ids": [copy_node["id"], image_node["id"], output_node["id"]],
        },
    )

    assert saved.status_code == 201
    summary = saved.json()
    assert summary["source"] == "user"
    assert summary["user_template_id"]
    assert summary["key"] == f"user:{summary['user_template_id']}"
    assert summary["kind"] == "node_group"
    assert summary["version"] == 1
    assert summary["title"] == "common main image link"
    assert [node["position_x"] for node in summary["preview_nodes"]] == [
        copy_node["position_x"] - copy_node["position_x"],
        image_node["position_x"] - copy_node["position_x"],
        output_node["position_x"] - copy_node["position_x"],
    ]

    listed = client.get("/api/workflow/canvas-templates")
    assert listed.status_code == 200
    listed_template = next(item for item in listed.json()["items"] if item["key"] == summary["key"])
    assert listed_template["title"] == "common main image link"
    assert listed_template["source"] == "user"

    renamed = client.patch(
        f"/api/workflow/user-template-groups/{summary['user_template_id']}",
        json={"title": "renamed main image link", "description": "new note"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "renamed main image link"
    assert renamed.json()["description"] == "new note"

    before_apply = client.get(f"/api/products/{product_id}/workflow").json()
    previous_node_ids = {node["id"] for node in before_apply["nodes"]}
    applied = client.post(
        f"/api/products/{product_id}/workflow/template-groups",
        json={"template_key": summary["key"], "position_x": 900, "position_y": 460},
    )

    assert applied.status_code == 201
    applied_workflow = applied.json()
    created_nodes = [node for node in applied_workflow["nodes"] if node["id"] not in previous_node_ids]
    created_node_ids = {node["id"] for node in created_nodes}
    assert len(created_nodes) == 3
    assert {node["node_type"] for node in created_nodes} == {
        "copy_generation",
        "image_generation",
        "reference_image",
    }
    assert all(node["output_json"] is None for node in created_nodes)
    assert any(
        edge["source_node_id"] in created_node_ids and edge["target_node_id"] in created_node_ids
        for edge in applied_workflow["edges"]
    )

    session = get_session_factory()()
    try:
        template_row = session.get(UserCanvasTemplate, summary["user_template_id"])
        assert template_row is not None
        assert template_row.schema_version == 1
        assert template_row.template_json["version"] == 1
        assert template_row.template_json["kind"] == "node_group"
        assert not _contains_key(template_row.template_json, "output_json")
        workflow_node_count = session.query(WorkflowNode).filter_by(workflow_id=applied_workflow["id"]).count()
        workflow_edge_count = session.query(WorkflowEdge).filter_by(workflow_id=applied_workflow["id"]).count()
        assert workflow_node_count == len(applied_workflow["nodes"])
        assert workflow_edge_count == len(applied_workflow["edges"])
    finally:
        session.close()

    archived = client.delete(f"/api/workflow/user-template-groups/{summary['user_template_id']}")
    assert archived.status_code == 204
    listed_after_archive = client.get("/api/workflow/canvas-templates").json()
    assert summary["key"] not in {item["key"] for item in listed_after_archive["items"]}
    apply_archived = client.post(
        f"/api/products/{product_id}/workflow/template-groups",
        json={"template_key": summary["key"], "position_x": 900, "position_y": 460},
    )
    assert apply_archived.status_code == 400
    assert apply_archived.json()["detail"] == "Canvas template not found"


def test_user_template_group_preserves_unrun_prompt_config_when_applied(configured_env: Path) -> None:
    from productflow_backend.infrastructure.db.models import UserCanvasTemplate
    from productflow_backend.infrastructure.db.session import get_session_factory
    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)
    _login(client)
    created = client.post(
        "/api/products",
        data={"name": "template not runproduct"},
        files={"image": ("unrun-template.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]
    workflow = client.get(f"/api/products/{product_id}/workflow").json()
    copy_node = next(node for node in workflow["nodes"] if node["node_type"] == "copy_generation")
    image_node = next(node for node in workflow["nodes"] if node["node_type"] == "image_generation")

    copy_config = {
        "instruction": "not runningcopyprompt：emphasize portability、waterproof and gift box scene",
        "tone": "heightlevel restrained",
        "channel": "detail pagemain image",
    }
    image_config = {
        "instruction": "not runningimage generationprompt：Generate a gift set on a light gray backgroundmain image，soft lighting，subject centered",
        "size": "1536x1024",
    }
    copy_updated = client.patch(f"/api/workflow-nodes/{copy_node['id']}", json={"config_json": copy_config})
    assert copy_updated.status_code == 200
    image_updated = client.patch(f"/api/workflow-nodes/{image_node['id']}", json={"config_json": image_config})
    assert image_updated.status_code == 200
    unrun_workflow = image_updated.json()
    unrun_copy = next(node for node in unrun_workflow["nodes"] if node["id"] == copy_node["id"])
    unrun_image = next(node for node in unrun_workflow["nodes"] if node["id"] == image_node["id"])
    assert unrun_copy["status"] == "idle"
    assert unrun_copy["output_json"] is None
    assert unrun_image["status"] == "idle"
    assert unrun_image["output_json"] is None

    saved = client.post(
        f"/api/products/{product_id}/workflow/user-template-groups",
        json={
            "title": "not runningprompttemplate",
            "node_ids": [copy_node["id"], image_node["id"]],
        },
    )
    assert saved.status_code == 201
    template_id = saved.json()["user_template_id"]
    template_key = saved.json()["key"]

    session = get_session_factory()()
    try:
        template = session.get(UserCanvasTemplate, template_id)
        assert template is not None
        template_nodes = {node["node_type"]: node for node in template.template_json["nodes"]}
        assert template_nodes["copy_generation"]["config_json"] == {
            **copy_config,
            "version": 2,
            "output_mode": "freeform",
            "purpose": None,
            "requested_slots": [],
        }
        assert template_nodes["image_generation"]["config_json"] == image_config
        assert not _contains_key(template.template_json, "output_json")
    finally:
        session.close()

    before_apply = client.get(f"/api/products/{product_id}/workflow").json()
    previous_node_ids = {node["id"] for node in before_apply["nodes"]}
    applied = client.post(
        f"/api/products/{product_id}/workflow/template-groups",
        json={"template_key": template_key, "position_x": 1040, "position_y": 520},
    )
    assert applied.status_code == 201
    created_nodes = [node for node in applied.json()["nodes"] if node["id"] not in previous_node_ids]
    created_by_type = {node["node_type"]: node for node in created_nodes}
    assert created_by_type["copy_generation"]["config_json"] == {
        **copy_config,
        "version": 2,
        "output_mode": "freeform",
        "purpose": None,
        "requested_slots": [],
    }
    assert created_by_type["copy_generation"]["status"] == "idle"
    assert created_by_type["copy_generation"]["output_json"] is None
    assert created_by_type["image_generation"]["config_json"] == image_config
    assert created_by_type["image_generation"]["status"] == "idle"
    assert created_by_type["image_generation"]["output_json"] is None


def test_user_template_group_sanitizes_artifact_config_and_rejects_product_context(configured_env: Path) -> None:
    from productflow_backend.infrastructure.db.models import UserCanvasTemplate, WorkflowNode
    from productflow_backend.infrastructure.db.session import get_session_factory
    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)
    _login(client)
    created = client.post(
        "/api/products",
        data={"name": "rejected templateproduct"},
        files={"image": ("reject-user-template.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]
    workflow = client.get(f"/api/products/{product_id}/workflow").json()
    context_node = next(node for node in workflow["nodes"] if node["node_type"] == "product_context")
    reference_node = next(node for node in workflow["nodes"] if node["node_type"] == "reference_image")

    empty_save = client.post(
        f"/api/products/{product_id}/workflow/user-template-groups",
        json={"title": "empty template", "node_ids": []},
    )
    assert empty_save.status_code == 400
    assert empty_save.json()["detail"] == "Please select nodes to save"

    context_save = client.post(
        f"/api/products/{product_id}/workflow/user-template-groups",
        json={"title": "error template", "node_ids": [context_node["id"]]},
    )
    assert context_save.status_code == 400
    assert context_save.json()["detail"] == "Node group templates cannot include the product info node"

    polluted = client.patch(
        f"/api/workflow-nodes/{reference_node['id']}",
        json={
            "config_json": {
                "role": "reference",
                "label": "kept tag",
                "_canvas_template": {
                    "source": "builtin",
                    "template_key": "ecommerce-main-image-v1",
                    "node_key": "output",
                },
                "source_asset_ids": ["asset-1"],
                "source_poster_variant_id": "poster-1",
            }
        },
    )
    assert polluted.status_code == 200
    sanitized = client.post(
        f"/api/products/{product_id}/workflow/user-template-groups",
        json={"title": "cleaned template", "node_ids": [reference_node["id"]]},
    )
    assert sanitized.status_code == 201

    session = get_session_factory()()
    try:
        template = session.get(UserCanvasTemplate, sanitized.json()["user_template_id"])
        assert template is not None
        assert template.template_json["nodes"][0]["config_json"] == {"role": "reference", "label": "kept tag"}
        assert not _contains_value(template.template_json, "asset-1")
        assert not _contains_value(template.template_json, "poster-1")
    finally:
        session.close()

    unsafe = client.patch(
        f"/api/workflow-nodes/{reference_node['id']}",
        json={"config_json": {"role": "reference", "unknown_external_id": "external-1"}},
    )
    assert unsafe.status_code == 200
    rejected = client.post(
        f"/api/products/{product_id}/workflow/user-template-groups",
        json={"title": "unknownartifact template", "node_ids": [reference_node["id"]]},
    )
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == "Template configuration contains non-reusable artifact data"

    workflow = client.get(f"/api/products/{product_id}/workflow").json()
    image_node = next(node for node in workflow["nodes"] if node["node_type"] == "image_generation")
    session = get_session_factory()()
    try:
        persisted_image_node = session.get(WorkflowNode, image_node["id"])
        assert persisted_image_node is not None
        persisted_image_node.config_json = {"size": "bad-size"}
        session.commit()
    finally:
        session.close()
    invalid_size_save = client.post(
        f"/api/products/{product_id}/workflow/user-template-groups",
        json={"title": "abnormal size template", "node_ids": [image_node["id"]]},
    )
    assert invalid_size_save.status_code == 400
    assert invalid_size_save.json() == {"detail": "image generation size must use widthxheight format, e.g. 1024x1024"}


def test_user_template_group_ignores_node_outputs_when_saving(configured_env: Path) -> None:
    from productflow_backend.infrastructure.db.models import UserCanvasTemplate, WorkflowNode
    from productflow_backend.infrastructure.db.session import get_session_factory
    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)
    _login(client)
    created = client.post(
        "/api/products",
        data={"name": "output not in template product"},
        files={"image": ("output-template.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]
    workflow = client.get(f"/api/products/{product_id}/workflow").json()
    copy_node = next(node for node in workflow["nodes"] if node["node_type"] == "copy_generation")

    session = get_session_factory()()
    try:
        node = session.get(WorkflowNode, copy_node["id"])
        assert node is not None
        node.output_json = {
            "copy_set_id": "copy-artifact",
            "poster_variant_id": "poster-artifact",
            "generated_poster_variant_ids": ["poster-artifact"],
            "filled_source_asset_ids": ["asset-artifact"],
            "filled_reference_node_ids": ["node-artifact"],
            "summary": "this Run output should not enter the template",
        }
        session.commit()
    finally:
        session.close()

    saved = client.post(
        f"/api/products/{product_id}/workflow/user-template-groups",
        json={"title": "config only stored", "node_ids": [copy_node["id"]]},
    )
    assert saved.status_code == 201
    template_id = saved.json()["user_template_id"]

    session = get_session_factory()()
    try:
        template = session.get(UserCanvasTemplate, template_id)
        assert template is not None
        assert not _contains_value(template.template_json, "copy-artifact")
        assert not _contains_value(template.template_json, "poster-artifact")
        assert not _contains_value(template.template_json, "asset-artifact")
        assert not _contains_key(template.template_json, "output_json")
        assert not _contains_key(template.template_json, "copy_set_id")
        assert not _contains_key(template.template_json, "poster_variant_id")
        assert not _contains_key(template.template_json, "generated_poster_variant_ids")
    finally:
        session.close()


def _contains_key(value: object, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


def _contains_value(value: object, expected: object) -> bool:
    if value == expected:
        return True
    if isinstance(value, dict):
        return any(_contains_value(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(_contains_value(item, expected) for item in value)
    return False


def test_image_generation_node_normalizes_custom_size_and_rejects_unsafe_dimensions(configured_env: Path) -> None:
    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post(
        "/api/products",
        data={"name": "custom sizeproduct"},
        files={"image": ("product.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]

    workflow_response = client.get(f"/api/products/{product_id}/workflow")
    assert workflow_response.status_code == 200
    image_node = next(node for node in workflow_response.json()["nodes"] if node["node_type"] == "image_generation")

    updated = client.patch(
        f"/api/workflow-nodes/{image_node['id']}",
        json={"config_json": {"instruction": "generate widescreen product scene", "size": "3840X2160"}},
    )
    assert updated.status_code == 200
    updated_image_node = next(node for node in updated.json()["nodes"] if node["id"] == image_node["id"])
    assert updated_image_node["config_json"]["size"] == "3840x2160"

    non_multiple = client.patch(
        f"/api/workflow-nodes/{image_node['id']}",
        json={"config_json": {"instruction": "provider 16 multiple calibration", "size": "1500x800"}},
    )
    assert non_multiple.status_code == 200
    non_multiple_image_node = next(node for node in non_multiple.json()["nodes"] if node["id"] == image_node["id"])
    assert non_multiple_image_node["config_json"]["size"] == "1504x800"

    undersized = client.patch(
        f"/api/workflow-nodes/{image_node['id']}",
        json={"config_json": {"instruction": "provider lower bound fallback", "size": "64x64"}},
    )
    assert undersized.status_code == 200
    undersized_image_node = next(node for node in undersized.json()["nodes"] if node["id"] == image_node["id"])
    assert undersized_image_node["config_json"]["size"] == "512x512"

    invalid_zero = client.patch(
        f"/api/workflow-nodes/{image_node['id']}",
        json={"config_json": {"instruction": "invalid size", "size": "0x2160"}},
    )
    assert invalid_zero.status_code == 400
    assert "width and height must be greater than 0" in invalid_zero.json()["detail"]

    oversized = client.patch(
        f"/api/workflow-nodes/{image_node['id']}",
        json={"config_json": {"instruction": "oversized", "size": "5000x5000"}},
    )
    assert oversized.status_code == 200
    oversized_image_node = next(node for node in oversized.json()["nodes"] if node["id"] == image_node["id"])
    assert oversized_image_node["config_json"]["size"] == "3840x3840"


def test_product_workflow_singleton_context_and_direct_image_run(configured_env: Path) -> None:
    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post(
        "/api/products",
        data={"name": "direct run lamp"},
        files={"image": ("lamp.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]

    list_response = client.get("/api/products?page=1&page_size=1")
    assert list_response.status_code == 200
    listed = list_response.json()
    assert listed["total"] == 1
    summary = listed["items"][0]
    assert summary["source_image_filename"] == "lamp.png"
    assert summary["source_image_thumbnail_url"].endswith("variant=thumbnail")

    workflow_response = client.get(f"/api/products/{product_id}/workflow")
    assert workflow_response.status_code == 200
    workflow = workflow_response.json()
    context_node = next(node for node in workflow["nodes"] if node["node_type"] == "product_context")
    image_node = next(node for node in workflow["nodes"] if node["node_type"] == "image_generation")

    duplicate_context = client.post(
        f"/api/products/{product_id}/workflow/nodes",
        json={
            "node_type": "product_context",
            "title": "duplicateproduct",
            "position_x": 120,
            "position_y": 120,
            "config_json": {},
        },
    )
    assert duplicate_context.status_code == 400
    assert duplicate_context.json()["detail"] == "Product info node already exists"

    session = get_session_factory()()
    try:
        persisted_workflow = session.scalar(sa.select(ProductWorkflow).where(ProductWorkflow.product_id == product_id))
        assert persisted_workflow is not None
        duplicate_node = WorkflowNode(
            workflow_id=persisted_workflow.id,
            node_type=WorkflowNodeType.PRODUCT_CONTEXT,
            title="historical duplicateproduct",
            position_x=180,
            position_y=140,
            config_json={},
        )
        session.add(duplicate_node)
        session.commit()
    finally:
        session.close()

    normalized_response = client.get(f"/api/products/{product_id}/workflow")
    assert normalized_response.status_code == 200
    normalized_workflow = normalized_response.json()
    assert [node["node_type"] for node in normalized_workflow["nodes"]].count("product_context") == 1

    removable_nodes = [
        node for node in normalized_workflow["nodes"] if node["node_type"] in {"copy_generation", "reference_image"}
    ]
    for removable in removable_nodes:
        deleted = client.delete(f"/api/workflow-nodes/{removable['id']}")
        assert deleted.status_code == 200

    patched_image = client.patch(
        f"/api/workflow-nodes/{image_node['id']}",
        json={"config_json": {"instruction": "only based onproduct infogeneratecleanmain image", "size": "1024x1024"}},
    )
    assert patched_image.status_code == 200

    run_response = client.post(
        f"/api/products/{product_id}/workflow/run",
        json={"start_node_id": image_node["id"]},
    )
    assert run_response.status_code == 200
    payload = _wait_for_workflow_run(client, product_id, status="failed")
    assert [node["node_type"] for node in payload["nodes"]] == ["product_context", "image_generation"]
    image_node_after = next(node for node in payload["nodes"] if node["id"] == image_node["id"])
    assert image_node_after["status"] == "failed"
    assert "at least one image or reference image node" in image_node_after["failure_reason"]
    assert next(node for node in payload["nodes"] if node["id"] == context_node["id"])["node_type"] == "product_context"

def test_direct_downstream_run_uses_latest_saved_product_context(configured_env: Path) -> None:
    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post(
        "/api/products",
        data={"name": "travel backpack"},
        files={"image": ("bag.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]

    workflow_response = client.get(f"/api/products/{product_id}/workflow")
    assert workflow_response.status_code == 200
    workflow = workflow_response.json()
    context_node = next(node for node in workflow["nodes"] if node["node_type"] == "product_context")
    image_node = next(node for node in workflow["nodes"] if node["node_type"] == "image_generation")

    initial_context = client.patch(
        f"/api/workflow-nodes/{context_node['id']}",
        json={
            "config_json": {
                "name": "travel backpack",
                "category": "oldcategory",
                "price": "199",
                "source_note": "old note：city commute。",
            }
        },
    )
    assert initial_context.status_code == 200
    first_run = client.post(f"/api/products/{product_id}/workflow/run", json={})
    assert first_run.status_code == 200
    first_payload = _wait_for_workflow_run(client, product_id, status="succeeded")
    stale_context_output = next(
        node for node in first_payload["nodes"] if node["id"] == context_node["id"]
    )["output_json"]
    assert stale_context_output["source_note"] == "old note：city commute。"

    latest_context = client.patch(
        f"/api/workflow-nodes/{context_node['id']}",
        json={
            "config_json": {
                "name": "travel backpack",
                "category": "outdoor gear",
                "price": "249",
                "source_note": "latest note：splash-proof Oxford fabric，suits short business trips and weekend camping。",
            }
        },
    )
    assert latest_context.status_code == 200

    selected_run = client.post(
        f"/api/products/{product_id}/workflow/run",
        json={"start_node_id": image_node["id"]},
    )
    assert selected_run.status_code == 200
    payload = _wait_for_workflow_run(client, product_id, status="succeeded")
    image_output = next(node for node in payload["nodes"] if node["id"] == image_node["id"])["output_json"]

    assert image_output["context_summary"]["product_context"]["category"] == "outdoor gear"
    assert image_output["context_summary"]["product_context"]["price"] == "249"
    assert (
        image_output["context_summary"]["product_context"]["source_note"]
        == "latest note：splash-proof Oxford fabric，suits short business trips and weekend camping。"
    )
    assert any("latest note" in source["text"] for source in image_output["context_sources"])


def test_product_context_ignores_unresolved_placeholder_values(
    configured_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.infrastructure.image.base import GeneratedImagePayload
    from productflow_backend.presentation.api import create_app

    session = get_session_factory()()
    try:
        session.add(AppSetting(key="poster_generation_mode", value="generated"))
        session.commit()
    finally:
        session.close()

    captured_inputs: list[PosterGenerationInput] = []

    class CapturingImageProvider:
        provider_name = "capturing"
        prompt_version = "capturing-v1"

        def generate_poster_image(
            self,
            poster: PosterGenerationInput,
            kind: PosterKind,
        ) -> tuple[GeneratedImagePayload, str]:
            captured_inputs.append(poster)
            return (
                GeneratedImagePayload(
                    kind=kind,
                    bytes_data=_make_demo_image_bytes(),
                    mime_type="image/png",
                    width=800,
                    height=800,
                    variant_label="capturing-placeholder-filter",
                ),
                "capturing-v1",
            )

    _execute_workflow_queue_inline(
        monkeypatch,
        dependencies=WorkflowExecutionDependencies(
            image_provider_resolver=CapturingImageProvider,
        ),
    )

    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post(
        "/api/products",
        data={"name": "test phone case"},
        files={"image": ("case.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]

    workflow_response = client.get(f"/api/products/{product_id}/workflow")
    assert workflow_response.status_code == 200
    workflow = workflow_response.json()
    context_node = next(node for node in workflow["nodes"] if node["node_type"] == "product_context")
    copy_node = next(node for node in workflow["nodes"] if node["node_type"] == "copy_generation")
    image_node = next(node for node in workflow["nodes"] if node["node_type"] == "image_generation")

    deleted_copy = client.delete(f"/api/workflow-nodes/{copy_node['id']}")
    assert deleted_copy.status_code == 200

    patched_context = client.patch(
        f"/api/workflow-nodes/{context_node['id']}",
        json={
            "config_json": {
                "name": "test phone case",
                "category": "{category}",
                "price": "{price}",
                "source_note": "{source_note}",
            }
        },
    )
    assert patched_context.status_code == 200

    selected_run = client.post(
        f"/api/products/{product_id}/workflow/run",
        json={"start_node_id": image_node["id"]},
    )
    assert selected_run.status_code == 200
    payload = _wait_for_workflow_run(client, product_id, status="succeeded")
    image_output = next(node for node in payload["nodes"] if node["id"] == image_node["id"])["output_json"]

    assert image_output["context_summary"]["product_context"] == {
        "name": "test phone case",
        "category": None,
        "price": None,
        "source_note": None,
    }
    assert not any("{category}" in source["text"] for source in image_output["context_sources"])
    assert not any("{price}" in source["text"] for source in image_output["context_sources"])
    assert not any("{source_note}" in source["text"] for source in image_output["context_sources"])
    assert len(captured_inputs) == 1
    provider_input = captured_inputs[0]
    assert image_output["context_summary"]["copy_prompt_mode"] == "image_edit"
    assert provider_input.copy_prompt_mode == "image_edit"
    assert provider_input.product_name == "test phone case"
    assert provider_input.category is None
    assert provider_input.price is None
    assert provider_input.source_note is None
    assert provider_input.structured_copy_context is None


def test_product_context_source_image_reaches_image_generation_context(
    configured_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.infrastructure.image.base import GeneratedImagePayload
    from productflow_backend.presentation.api import create_app

    session = get_session_factory()()
    try:
        session.add(AppSetting(key="poster_generation_mode", value="generated"))
        session.commit()
    finally:
        session.close()

    captured_inputs: list[PosterGenerationInput] = []

    class CapturingImageProvider:
        provider_name = "capturing"
        prompt_version = "capturing-v1"

        def generate_poster_image(
            self,
            poster: PosterGenerationInput,
            kind: PosterKind,
        ) -> tuple[GeneratedImagePayload, str]:
            captured_inputs.append(poster)
            return (
                GeneratedImagePayload(
                    kind=kind,
                    bytes_data=_make_demo_image_bytes(),
                    mime_type="image/png",
                    width=800,
                    height=800,
                    variant_label=f"capturing-r{len(poster.reference_images)}",
                ),
                "capturing-v1",
            )

    _execute_workflow_queue_inline(
        monkeypatch,
        dependencies=WorkflowExecutionDependencies(
            image_provider_resolver=CapturingImageProvider,
        ),
    )

    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post(
        "/api/products",
        data={"name": "travel backpack"},
        files={"image": ("bag.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]

    workflow_response = client.get(f"/api/products/{product_id}/workflow")
    assert workflow_response.status_code == 200
    workflow = workflow_response.json()
    context_node = next(node for node in workflow["nodes"] if node["node_type"] == "product_context")
    image_node = next(node for node in workflow["nodes"] if node["node_type"] == "image_generation")
    assert any(
        edge["source_node_id"] == context_node["id"] and edge["target_node_id"] == image_node["id"]
        for edge in workflow["edges"]
    )
    patched_image = client.patch(
        f"/api/workflow-nodes/{image_node['id']}",
        json={
            "config_json": {
                "instruction": "generate widescreen product scene",
                "size": "3840x2160",
                "tool_options": {
                    "quality": "high",
                    "output_format": "webp",
                    "input_fidelity": "high",
                },
            }
        },
    )
    assert patched_image.status_code == 200

    selected_run = client.post(
        f"/api/products/{product_id}/workflow/run",
        json={"start_node_id": image_node["id"]},
    )
    assert selected_run.status_code == 200
    payload = _wait_for_workflow_run(client, product_id, status="succeeded")
    image_output = next(node for node in payload["nodes"] if node["id"] == image_node["id"])["output_json"]

    assert image_output["context_summary"]["reference_image_count"] == 1
    assert any(
        source["label"] == "product image" and "bag.png" in source["text"]
        for source in image_output["context_sources"]
    )
    assert image_output["context_summary"]["copy_prompt_mode"] == "copy"
    assert len(captured_inputs) == 1
    provider_input = captured_inputs[0]
    assert provider_input.copy_prompt_mode == "copy"
    assert provider_input.image_size == "3840x2160"
    assert provider_input.tool_options == {
        "quality": "high",
        "output_format": "webp",
        "input_fidelity": "high",
    }
    assert len(provider_input.reference_images) == 1
    assert provider_input.reference_images[0].path == provider_input.source_image


def test_image_generation_collects_product_context_through_upstream_copy_edge(
    configured_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.infrastructure.image.base import GeneratedImagePayload
    from productflow_backend.presentation.api import create_app

    session = get_session_factory()()
    try:
        session.add(AppSetting(key="poster_generation_mode", value="generated"))
        session.commit()
    finally:
        session.close()

    captured_inputs: list[PosterGenerationInput] = []

    class CapturingImageProvider:
        provider_name = "capturing"
        prompt_version = "capturing-v1"

        def generate_poster_image(
            self,
            poster: PosterGenerationInput,
            kind: PosterKind,
        ) -> tuple[GeneratedImagePayload, str]:
            captured_inputs.append(poster)
            return (
                GeneratedImagePayload(
                    kind=kind,
                    bytes_data=_make_demo_image_bytes(),
                    mime_type="image/png",
                    width=800,
                    height=800,
                    variant_label=f"capturing-r{len(poster.reference_images)}",
                ),
                "capturing-v1",
            )

    _execute_workflow_queue_inline(
        monkeypatch,
        dependencies=WorkflowExecutionDependencies(
            image_provider_resolver=CapturingImageProvider,
        ),
    )

    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post(
        "/api/products",
        data={"name": "folding camping chair"},
        files={"image": ("chair.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]

    workflow_response = client.get(f"/api/products/{product_id}/workflow")
    assert workflow_response.status_code == 200
    workflow = workflow_response.json()
    context_node = next(node for node in workflow["nodes"] if node["node_type"] == "product_context")
    copy_node = next(node for node in workflow["nodes"] if node["node_type"] == "copy_generation")
    image_node = next(node for node in workflow["nodes"] if node["node_type"] == "image_generation")
    direct_context_edge = next(
        edge
        for edge in workflow["edges"]
        if edge["source_node_id"] == context_node["id"] and edge["target_node_id"] == image_node["id"]
    )
    assert any(
        edge["source_node_id"] == context_node["id"] and edge["target_node_id"] == copy_node["id"]
        for edge in workflow["edges"]
    )
    assert any(
        edge["source_node_id"] == copy_node["id"] and edge["target_node_id"] == image_node["id"]
        for edge in workflow["edges"]
    )

    deleted_direct_edge = client.delete(f"/api/workflow-edges/{direct_context_edge['id']}")
    assert deleted_direct_edge.status_code == 200
    patched_context = client.patch(
        f"/api/workflow-nodes/{context_node['id']}",
        json={
            "config_json": {
                "name": "folding camping chair",
                "category": "outdoor furniture",
                "price": "129",
                "source_note": "aluminum bracket，foldable storage，suits camping and balcony rest。",
            }
        },
    )
    assert patched_context.status_code == 200

    selected_run = client.post(
        f"/api/products/{product_id}/workflow/run",
        json={"start_node_id": image_node["id"]},
    )
    assert selected_run.status_code == 200
    payload = _wait_for_workflow_run(client, product_id, status="succeeded")
    image_output = next(node for node in payload["nodes"] if node["id"] == image_node["id"])["output_json"]

    assert image_output["context_summary"]["product_context"]["category"] == "outdoor furniture"
    assert image_output["context_summary"]["product_context"]["price"] == "129"
    assert image_output["context_summary"]["reference_image_count"] == 1
    assert any("folding camping chair" in source["text"] for source in image_output["context_sources"])
    assert any(
        source["label"] == "product image" and "chair.png" in source["text"]
        for source in image_output["context_sources"]
    )
    assert len(captured_inputs) == 1
    provider_input = captured_inputs[0]
    assert provider_input.copy_prompt_mode == "copy"
    assert provider_input.product_name == "folding camping chair"
    assert provider_input.category == "outdoor furniture"
    assert provider_input.price == "129"
    assert provider_input.source_note == "aluminum bracket，foldable storage，suits camping and balcony rest。"
    assert provider_input.structured_copy_context is not None
    assert "workflow_context" not in provider_input.structured_copy_context
    assert "folding camping chair" in provider_input.structured_copy_context
    assert provider_input.source_image is not None
    assert len(provider_input.reference_images) == 1
    assert provider_input.reference_images[0].path == provider_input.source_image


def test_single_node_workflow_run_reuses_succeeded_upstream_outputs(configured_env: Path) -> None:
    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post(
        "/api/products",
        data={"name": "camping cup"},
        files={"image": ("cup.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]

    initial_workflow = client.get(f"/api/products/{product_id}/workflow")
    assert initial_workflow.status_code == 200
    workflow = initial_workflow.json()
    upstream_image_node = next(node for node in workflow["nodes"] if node["node_type"] == "image_generation")
    upstream_reference_node = next(node for node in workflow["nodes"] if node["node_type"] == "reference_image")
    upstream_slot_edge = client.post(
        f"/api/products/{product_id}/workflow/edges",
        json={
            "source_node_id": upstream_image_node["id"],
            "target_node_id": upstream_reference_node["id"],
            "source_handle": "output",
            "target_handle": "input",
        },
    )
    assert upstream_slot_edge.status_code == 201

    first_run = client.post(f"/api/products/{product_id}/workflow/run", json={})
    assert first_run.status_code == 200
    first_payload = _wait_for_workflow_run(client, product_id, status="succeeded")
    assert first_payload["runs"][0]["status"] == "succeeded"
    succeeded_image_node = next(node for node in first_payload["nodes"] if node["id"] == upstream_image_node["id"])
    succeeded_reference_node = next(
        node for node in first_payload["nodes"] if node["id"] == upstream_reference_node["id"]
    )
    upstream_poster_ids = succeeded_image_node["output_json"]["generated_poster_variant_ids"]
    upstream_reference_asset_ids = succeeded_reference_node["output_json"]["source_asset_ids"]
    assert upstream_poster_ids
    assert upstream_reference_asset_ids

    downstream_image = client.post(
        f"/api/products/{product_id}/workflow/nodes",
        json={
            "node_type": "image_generation",
            "title": "downstream image generation",
            "position_x": 900,
            "position_y": 360,
            "config_json": {"instruction": "follow upstreamimagecontinue generation", "size": "1024x1024"},
        },
    )
    assert downstream_image.status_code == 201
    downstream_image_node = next(node for node in downstream_image.json()["nodes"] if node["title"] == "downstream image generation")
    downstream_reference = client.post(
        f"/api/products/{product_id}/workflow/nodes",
        json={
            "node_type": "reference_image",
            "title": "downstream reference image",
            "position_x": 1180,
            "position_y": 360,
            "config_json": {"role": "reference", "label": "downstream reference image"},
        },
    )
    assert downstream_reference.status_code == 201
    downstream_reference_node = next(
        node for node in downstream_reference.json()["nodes"] if node["title"] == "downstream reference image"
    )
    upstream_edge = client.post(
        f"/api/products/{product_id}/workflow/edges",
        json={
            "source_node_id": upstream_image_node["id"],
            "target_node_id": downstream_image_node["id"],
            "source_handle": "output",
            "target_handle": "input",
        },
    )
    assert upstream_edge.status_code == 201
    target_edge = client.post(
        f"/api/products/{product_id}/workflow/edges",
        json={
            "source_node_id": downstream_image_node["id"],
            "target_node_id": downstream_reference_node["id"],
            "source_handle": "output",
            "target_handle": "input",
        },
    )
    assert target_edge.status_code == 201

    product_before = client.get(f"/api/products/{product_id}")
    assert product_before.status_code == 200
    copy_count_before = len(product_before.json()["copy_sets"])
    poster_count_before = len(product_before.json()["poster_variants"])
    source_asset_count_before = len(product_before.json()["source_assets"])

    single_run = client.post(
        f"/api/products/{product_id}/workflow/run",
        json={"start_node_id": downstream_image_node["id"]},
    )
    assert single_run.status_code == 200
    single_payload = _wait_for_workflow_run(client, product_id, status="succeeded")
    assert single_payload["runs"][0]["status"] == "succeeded"
    assert [node_run["node_id"] for node_run in single_payload["runs"][0]["node_runs"]] == [downstream_image_node["id"]]

    unchanged_upstream_image = next(node for node in single_payload["nodes"] if node["id"] == upstream_image_node["id"])
    unchanged_reference = next(node for node in single_payload["nodes"] if node["id"] == upstream_reference_node["id"])
    downstream_after = next(node for node in single_payload["nodes"] if node["id"] == downstream_image_node["id"])
    assert unchanged_upstream_image["output_json"]["generated_poster_variant_ids"] == upstream_poster_ids
    assert unchanged_reference["output_json"]["source_asset_ids"] == upstream_reference_asset_ids
    assert downstream_after["output_json"]["copy_set_id"] == unchanged_upstream_image["output_json"]["copy_set_id"]
    assert len(downstream_after["output_json"]["generated_poster_variant_ids"]) == 1
    assert "poster_variant_ids" not in downstream_after["output_json"]

    product_after = client.get(f"/api/products/{product_id}")
    assert product_after.status_code == 200
    assert len(product_after.json()["copy_sets"]) == copy_count_before
    assert len(product_after.json()["poster_variants"]) == poster_count_before + 1
    assert len(product_after.json()["source_assets"]) == source_asset_count_before + 1

def test_single_reference_run_reruns_upstream_when_target_slot_missing_artifact(configured_env: Path) -> None:
    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post(
        "/api/products",
        data={"name": "desk lamp"},
        files={"image": ("lamp.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]

    workflow_response = client.get(f"/api/products/{product_id}/workflow")
    assert workflow_response.status_code == 200
    workflow = workflow_response.json()
    image_node = next(node for node in workflow["nodes"] if node["node_type"] == "image_generation")

    first_run = client.post(f"/api/products/{product_id}/workflow/run", json={})
    assert first_run.status_code == 200
    assert _wait_for_workflow_run(client, product_id, status="succeeded")["runs"][0]["status"] == "succeeded"

    new_reference = client.post(
        f"/api/products/{product_id}/workflow/nodes",
        json={
            "node_type": "reference_image",
            "title": "added reference image",
            "position_x": 1180,
            "position_y": 380,
            "config_json": {"role": "reference", "label": "added reference image"},
        },
    )
    assert new_reference.status_code == 201
    new_reference_node = next(node for node in new_reference.json()["nodes"] if node["title"] == "added reference image")
    connected = client.post(
        f"/api/products/{product_id}/workflow/edges",
        json={
            "source_node_id": image_node["id"],
            "target_node_id": new_reference_node["id"],
            "source_handle": "output",
            "target_handle": "input",
        },
    )
    assert connected.status_code == 201

    product_before = client.get(f"/api/products/{product_id}")
    assert product_before.status_code == 200
    copy_count_before = len(product_before.json()["copy_sets"])

    slot_run = client.post(
        f"/api/products/{product_id}/workflow/run",
        json={"start_node_id": new_reference_node["id"]},
    )
    assert slot_run.status_code == 200
    payload = _wait_for_workflow_run(client, product_id, status="succeeded")
    assert payload["runs"][0]["status"] == "succeeded"
    assert {node_run["node_id"] for node_run in payload["runs"][0]["node_runs"]} == {
        image_node["id"],
        new_reference_node["id"],
    }
    filled_reference = next(node for node in payload["nodes"] if node["id"] == new_reference_node["id"])
    rerun_image = next(node for node in payload["nodes"] if node["id"] == image_node["id"])
    assert filled_reference["output_json"]["source_asset_ids"]
    assert new_reference_node["id"] in rerun_image["output_json"]["filled_reference_node_ids"]

    product_after = client.get(f"/api/products/{product_id}")
    assert product_after.status_code == 200
    assert len(product_after.json()["copy_sets"]) == copy_count_before

def test_image_generation_runs_without_product_context_edge(
    configured_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.infrastructure.image.base import GeneratedImagePayload
    from productflow_backend.presentation.api import create_app

    session = get_session_factory()()
    try:
        session.add(AppSetting(key="poster_generation_mode", value="generated"))
        session.commit()
    finally:
        session.close()

    captured_inputs: list[PosterGenerationInput] = []

    class CapturingImageProvider:
        provider_name = "capturing"
        prompt_version = "capturing-v1"

        def generate_poster_image(
            self,
            poster: PosterGenerationInput,
            kind: PosterKind,
        ) -> tuple[GeneratedImagePayload, str]:
            captured_inputs.append(poster)
            return (
                GeneratedImagePayload(
                    kind=kind,
                    bytes_data=_make_demo_image_bytes(),
                    mime_type="image/png",
                    width=800,
                    height=800,
                    variant_label="capturing-blank",
                ),
                "capturing-v1",
            )

    _execute_workflow_queue_inline(
        monkeypatch,
        dependencies=WorkflowExecutionDependencies(
            image_provider_resolver=CapturingImageProvider,
        ),
    )

    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post(
        "/api/products",
        data={"name": "not implicitly injectedproduct"},
        files={"image": ("source.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]
    session = get_session_factory()()
    try:
        product = session.get(Product, product_id)
        assert product is not None
        confirmed_copy = CopySet(
            product_id=product_id,
            status=CopyStatus.CONFIRMED,
            structured_payload={
                "version": 2,
                "summary": "poster title not to be inherited by blank generation",
                "content": {
                    "kind": "blocks",
                    "blocks": [{"id": "headline", "text": "title not to be inherited by blank generation"}],
                },
            },
            model_structured_payload={
                "version": 2,
                "summary": "poster title not to be inherited by blank generation",
                "content": {
                    "kind": "blocks",
                    "blocks": [{"id": "headline", "text": "title not to be inherited by blank generation"}],
                },
            },
            provider_name="test",
            model_name="test",
            prompt_version="test-v1",
        )
        session.add(confirmed_copy)
        session.flush()
        product.current_confirmed_copy_set_id = confirmed_copy.id
        session.commit()
    finally:
        session.close()

    workflow_response = client.get(f"/api/products/{product_id}/workflow")
    assert workflow_response.status_code == 200

    blank_image = client.post(
        f"/api/products/{product_id}/workflow/nodes",
        json={
            "node_type": "image_generation",
            "title": "blank image generation",
            "position_x": 620,
            "position_y": 420,
            "config_json": {"instruction": "free generate oneimageabstract blue gradient image", "size": "1280x720"},
        },
    )
    assert blank_image.status_code == 201
    image_node = next(node for node in blank_image.json()["nodes"] if node["title"] == "blank image generation")

    blank_target = client.post(
        f"/api/products/{product_id}/workflow/nodes",
        json={
            "node_type": "reference_image",
            "title": "blank result",
            "position_x": 920,
            "position_y": 420,
            "config_json": {"role": "reference", "label": "blank generation result"},
        },
    )
    assert blank_target.status_code == 201
    target_node = next(node for node in blank_target.json()["nodes"] if node["title"] == "blank result")

    target_edge = client.post(
        f"/api/products/{product_id}/workflow/edges",
        json={
            "source_node_id": image_node["id"],
            "target_node_id": target_node["id"],
            "source_handle": "output",
            "target_handle": "input",
        },
    )
    assert target_edge.status_code == 201

    selected_run = client.post(
        f"/api/products/{product_id}/workflow/run",
        json={"start_node_id": image_node["id"]},
    )
    assert selected_run.status_code == 200
    payload = _wait_for_workflow_run(client, product_id, status="succeeded")
    image_output = next(node for node in payload["nodes"] if node["id"] == image_node["id"])["output_json"]

    assert image_output["context_summary"]["product_context"] == {
        "name": None,
        "category": None,
        "price": None,
        "source_note": None,
    }
    assert image_output["context_summary"]["reference_image_count"] == 0
    assert not any(source["label"] == "product info" for source in image_output["context_sources"])
    assert len(captured_inputs) == 1
    provider_input = captured_inputs[0]
    assert provider_input.product_name == ""
    assert provider_input.structured_copy_context is None
    assert provider_input.source_image is None
    assert provider_input.reference_images == []
    assert provider_input.image_size == "1280x720"


def test_copy_generation_runs_without_product_context_edge(
    configured_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.presentation.api import create_app

    captured_products: list[ProductInput] = []

    class CapturingTextProvider:
        provider_name = "capturing-text"
        prompt_version = "capturing-text-v1"

        def generate_brief(self, product: ProductInput) -> tuple[CreativeBriefPayload, str]:
            captured_products.append(product)
            return (
                CreativeBriefPayload(
                    positioning="free creationpositioning",
                    audience="free creation audience",
                    selling_angles=["direction one", "direction two", "direction three"],
                    taboo_phrases=[],
                    poster_style_hint="free style",
                ),
                "capturing-brief",
            )

        def generate_copy(
            self,
            product: ProductInput,
            brief: CreativeBriefPayload,
            config: CopyNodeConfigV2,
            reference_images: list | None = None,
        ) -> tuple[CopyPayloadV2, str]:
            del brief, reference_images
            return (
                CopyPayloadV2(
                    summary="free creationposter title",
                    content=BlocksCopyContent(
                        blocks=[
                            CopyBlock(id="headline", text=f"{product.name} title"),
                            CopyBlock(id="point-1", text="freeselling point one"),
                            CopyBlock(id="point-2", text="freeselling point two"),
                            CopyBlock(id="point-3", text=config.instruction or "freeselling point three"),
                        ]
                    ),
                ),
                "capturing-copy",
            )

    _execute_workflow_queue_inline(
        monkeypatch,
        dependencies=WorkflowExecutionDependencies(
            text_provider_resolver=lambda: CapturingTextProvider(),
        ),
    )

    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post(
        "/api/products",
        data={"name": "should not be injected into isolatedcopyproduct"},
        files={"image": ("source.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]

    blank_copy = client.post(
        f"/api/products/{product_id}/workflow/nodes",
        json={
            "node_type": "copy_generation",
            "title": "blank copy",
            "position_x": 620,
            "position_y": 420,
            "config_json": {"instruction": "write a slogan that does not depend on product info"},
        },
    )
    assert blank_copy.status_code == 201
    copy_node = next(node for node in blank_copy.json()["nodes"] if node["title"] == "blank copy")

    selected_run = client.post(
        f"/api/products/{product_id}/workflow/run",
        json={"start_node_id": copy_node["id"]},
    )
    assert selected_run.status_code == 200
    payload = _wait_for_workflow_run(client, product_id, status="succeeded")
    copy_output = next(node for node in payload["nodes"] if node["id"] == copy_node["id"])["output_json"]

    assert captured_products
    captured_product = captured_products[0]
    assert captured_product.name == "Free-form creation"
    assert captured_product.category is None
    assert captured_product.price is None
    assert captured_product.source_note is None
    assert captured_product.image_path == ""
    assert copy_output["context_summary"]["product_context"] == {
        "name": None,
        "category": None,
        "price": None,
        "source_note": None,
    }
    assert not any(source["label"] == "product info" for source in copy_output["context_sources"])
