from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from helpers import _login, _make_demo_image_bytes

from productflow_backend.application.image_sessions import create_image_session, submit_image_session_generation_task
from productflow_backend.application.product_workflow import graph as product_workflow_graph
from productflow_backend.application.product_workflow.context import poster_kind_from_config
from productflow_backend.application.product_workflow.mutations import (
    create_workflow_edge,
    create_workflow_node,
    get_or_create_product_workflow,
)
from productflow_backend.application.use_cases import create_product, update_copy_set
from productflow_backend.domain.enums import WorkflowNodeType
from productflow_backend.domain.errors import (
    BusinessError,
    BusinessValidationError,
    NotFoundError,
    QueueUnavailableError,
)
from productflow_backend.infrastructure.db.models import CopySet
from productflow_backend.infrastructure.logging import current_log_context
from productflow_backend.presentation import errors as presentation_errors
from productflow_backend.presentation.api import create_app
from productflow_backend.presentation.errors import business_error_to_response


def test_typed_not_found_maps_to_404_without_message_suffix() -> None:
    response = business_error_to_response(NotFoundError("resource removed"))

    assert response.status_code == 404
    assert json.loads(response.body) == {"detail": "resource removed"}


def test_typed_business_error_maps_to_400() -> None:
    response = business_error_to_response(BusinessError("Please select an image"))

    assert response.status_code == 400
    assert json.loads(response.body) == {"detail": "Please select an image"}


def test_typed_poster_file_missing_remains_400() -> None:
    response = business_error_to_response(BusinessValidationError("poster file not found"))

    assert response.status_code == 400
    assert json.loads(response.body) == {"detail": "poster file not found"}


def test_typed_workflow_integrity_error_remains_400() -> None:
    response = business_error_to_response(BusinessValidationError("workflow edge references a missing node"))

    assert response.status_code == 400
    assert json.loads(response.body) == {"detail": "workflow edge references a missing node"}


def test_typed_queue_unavailable_maps_to_503() -> None:
    response = business_error_to_response(QueueUnavailableError("task queue temporarily unavailable，please retry later"))

    assert response.status_code == 503
    assert json.loads(response.body) == {"detail": "task queue temporarily unavailable，please retry later"}


def test_legacy_value_error_adapter_is_removed_after_route_migration() -> None:
    assert not hasattr(presentation_errors, "raise_value_error_as_http")


def test_global_business_error_handler_preserves_detail_shape(configured_env) -> None:  # noqa: ARG001
    app = create_app()
    assert BusinessError in app.exception_handlers
    assert ValueError not in app.exception_handlers
    assert Exception not in app.exception_handlers

    @app.get("/typed-not-found")
    def typed_not_found() -> None:
        assert current_log_context()["request_id"] == "typed-request-1"
        raise NotFoundError("resource removed")

    client = TestClient(app)
    response = client.get("/typed-not-found", headers={"X-Request-ID": "typed-request-1"})

    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == "typed-request-1"
    assert response.json() == {"detail": "resource removed"}
    assert "code" not in response.json()
    assert current_log_context()["request_id"] == "-"


def test_product_workflow_route_uses_global_business_error_handler(configured_env) -> None:  # noqa: ARG001
    app = create_app()
    client = TestClient(app)
    _login(client)

    response = client.get("/api/products/missing-product/workflow")

    assert response.status_code == 404
    assert response.json() == {"detail": "Product not found"}


def test_product_route_uses_global_business_error_handler(configured_env) -> None:  # noqa: ARG001
    app = create_app()
    client = TestClient(app)
    _login(client)

    missing = client.get("/api/products/missing-product")

    assert missing.status_code == 404
    assert missing.json() == {"detail": "Product not found"}
    assert "code" not in missing.json()

    invalid = client.post(
        "/api/products",
        data={"name": "   "},
        files={"image": ("blank.png", _make_demo_image_bytes(), "image/png")},
    )

    assert invalid.status_code == 400
    assert invalid.json() == {"detail": "Product name must not be empty"}
    assert "code" not in invalid.json()


def test_image_session_route_uses_global_business_error_handler(configured_env) -> None:  # noqa: ARG001
    app = create_app()
    client = TestClient(app)
    _login(client)

    missing = client.get("/api/image-sessions/missing-session")

    assert missing.status_code == 404
    assert missing.json() == {"detail": "Continuous-generation session not found"}
    assert "code" not in missing.json()

    created = client.post("/api/image-sessions", json={"title": "typed route image generation"})
    assert created.status_code == 201
    session_id = created.json()["id"]
    first = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "first round queue", "size": "1024x1024"},
    )
    assert first.status_code == 202
    invalid = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "second round missing base image", "size": "1024x1024"},
    )

    assert invalid.status_code == 400
    assert invalid.json() == {"detail": "Follow-up image generation must select a previously generated image from this session as the base image"}
    assert "code" not in invalid.json()


def test_gallery_route_uses_global_business_error_handler(configured_env) -> None:  # noqa: ARG001
    app = create_app()
    client = TestClient(app)
    _login(client)

    response = client.post("/api/gallery", json={"image_session_asset_id": "missing-asset"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Session image not found"}
    assert "code" not in response.json()


def test_high_risk_business_paths_raise_typed_validation_errors(db_session, configured_env) -> None:  # noqa: ARG001
    with pytest.raises(BusinessValidationError, match="Product name must not be empty"):
        create_product(
            db_session,
            name="   ",
            category=None,
            price=None,
            source_note=None,
            image_bytes=_make_demo_image_bytes(),
            filename="blank.png",
            content_type="image/png",
        )
    db_session.rollback()

    with pytest.raises(BusinessValidationError, match="Invalid price format"):
        create_product(
            db_session,
            name="price format error product",
            category=None,
            price="abc",
            source_note=None,
            image_bytes=_make_demo_image_bytes(),
            filename="invalid-price.png",
            content_type="image/png",
        )
    db_session.rollback()

    product = create_product(
        db_session,
        name="typed error product",
        category=None,
        price=None,
        source_note=None,
        image_bytes=_make_demo_image_bytes(),
        filename="typed.png",
        content_type="image/png",
    )
    with pytest.raises(BusinessValidationError, match="Product info node already exists"):
        create_workflow_node(
            db_session,
            product_id=product.id,
            node_type=WorkflowNodeType.PRODUCT_CONTEXT,
            title="duplicateproduct info",
            position_x=0,
            position_y=0,
            config_json={},
        )

    workflow = get_or_create_product_workflow(db_session, product.id)
    copy_node = next(node for node in workflow.nodes if node.node_type == WorkflowNodeType.COPY_GENERATION)
    image_node = next(node for node in workflow.nodes if node.node_type == WorkflowNodeType.IMAGE_GENERATION)
    with pytest.raises(BusinessValidationError, match="Workflow must not contain cyclic dependencies"):
        create_workflow_edge(
            db_session,
            product_id=product.id,
            source_node_id=image_node.id,
            target_node_id=copy_node.id,
        )
    db_session.rollback()

    image_session = create_image_session(db_session, product_id=None, title="typed error image generation")
    with pytest.raises(BusinessValidationError, match="Generation count must be between 1 and 10"):
        submit_image_session_generation_task(
            db_session,
            image_session_id=image_session.id,
            prompt="count out of bounds",
            size="1024x1024",
            generation_count=11,
        )

    with pytest.raises(BusinessValidationError, match="image generation node contains unsupported image type"):
        poster_kind_from_config({"poster_kind": "invalid"})


def test_update_copy_set_payload_validation_uses_typed_business_error(db_session, configured_env) -> None:  # noqa: ARG001
    product = create_product(
        db_session,
        name="bad copy payload product",
        category=None,
        price=None,
        source_note=None,
        image_bytes=_make_demo_image_bytes(),
        filename="bad-copy-payload.png",
        content_type="image/png",
    )
    copy_set = CopySet(
        product_id=product.id,
        structured_payload={
            "version": 2,
            "summary": "old copy",
            "content": {"kind": "freeform", "text": "old content"},
        },
        model_structured_payload={
            "version": 2,
            "summary": "old copy",
            "content": {"kind": "freeform", "text": "old content"},
        },
        provider_name="test",
        model_name="test",
        prompt_version="test",
    )
    db_session.add(copy_set)
    db_session.commit()

    with pytest.raises(BusinessValidationError, match="Copy model output must conform to the CopyPayloadV2 contract"):
        update_copy_set(db_session, copy_set_id=copy_set.id, structured_payload={"invalid": True})


def test_workflow_edge_rollback_preserves_typed_business_errors(
    db_session,
    configured_env,  # noqa: ARG001
    monkeypatch,
) -> None:
    product = create_product(
        db_session,
        name="typed edge error product",
        category=None,
        price=None,
        source_note=None,
        image_bytes=_make_demo_image_bytes(),
        filename="typed-edge.png",
        content_type="image/png",
    )
    workflow = get_or_create_product_workflow(db_session, product.id)
    copy_node = next(node for node in workflow.nodes if node.node_type == WorkflowNodeType.COPY_GENERATION)
    reference_node = next(node for node in workflow.nodes if node.node_type == WorkflowNodeType.REFERENCE_IMAGE)

    def raise_not_found(_workflow):
        raise NotFoundError("workflow not found")

    monkeypatch.setattr(product_workflow_graph, "topological_nodes", raise_not_found)

    with pytest.raises(NotFoundError, match="workflow not found"):
        create_workflow_edge(
            db_session,
            product_id=product.id,
            source_node_id=copy_node.id,
            target_node_id=reference_node.id,
        )
