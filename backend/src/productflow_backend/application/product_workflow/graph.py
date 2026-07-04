from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, load_only, selectinload

from productflow_backend.application.admission import (
    get_generation_queue_overview,
    get_queued_generation_positions,
    get_workflow_run_queue_metadata,
)
from productflow_backend.domain.enums import WorkflowNodeType, WorkflowRunStatus
from productflow_backend.domain.errors import NotFoundError
from productflow_backend.domain.workflow_rules import WorkflowRuleEdge, WorkflowRuleNode, topological_node_ids
from productflow_backend.infrastructure.db.models import (
    Product,
    ProductWorkflow,
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
)

DEFAULT_WORKFLOW_TITLE = "ProductCreative workflow"
DEFAULT_IMAGE_SIZE = "1024x1024"


@dataclass(frozen=True, slots=True)
class ProductWorkflowStatusSnapshot:
    workflow: ProductWorkflow
    nodes: list[WorkflowNode]
    runs: list[WorkflowRun]
    node_context_runs: list[WorkflowRun]


def workflow_query():
    return select(ProductWorkflow).options(
        selectinload(ProductWorkflow.product).selectinload(Product.source_assets),
        selectinload(ProductWorkflow.product).selectinload(Product.creative_briefs),
        selectinload(ProductWorkflow.product).selectinload(Product.copy_sets),
        selectinload(ProductWorkflow.product).selectinload(Product.poster_variants),
        selectinload(ProductWorkflow.product).selectinload(Product.confirmed_copy_set),
        selectinload(ProductWorkflow.nodes),
        selectinload(ProductWorkflow.edges),
        selectinload(ProductWorkflow.runs).selectinload(WorkflowRun.node_runs),
    )


def workflow_status_query():
    return select(ProductWorkflow).options(
        load_only(
            ProductWorkflow.id,
            ProductWorkflow.product_id,
            ProductWorkflow.title,
            ProductWorkflow.active,
            ProductWorkflow.created_at,
            ProductWorkflow.updated_at,
        ),
    )


def get_product_or_raise(session: Session, product_id: str) -> Product:
    product = session.scalar(
        select(Product)
        .options(
            selectinload(Product.source_assets),
            selectinload(Product.creative_briefs),
            selectinload(Product.copy_sets),
            selectinload(Product.poster_variants),
            selectinload(Product.confirmed_copy_set),
        )
        .where(Product.id == product_id)
    )
    if product is None:
        raise NotFoundError("Product not found")
    return product


def get_workflow_or_raise(session: Session, workflow_id: str) -> ProductWorkflow:
    workflow = session.scalar(workflow_query().where(ProductWorkflow.id == workflow_id))
    if workflow is None:
        raise NotFoundError("Workflow not found")
    attach_workflow_run_queue_metadata(session, workflow.runs)
    return workflow


def get_active_workflow(session: Session, product_id: str) -> ProductWorkflow | None:
    workflow = session.scalar(
        workflow_query().where(ProductWorkflow.product_id == product_id, ProductWorkflow.active.is_(True))
    )
    if workflow is not None:
        attach_workflow_run_queue_metadata(session, workflow.runs)
    return workflow


def attach_workflow_run_queue_metadata(session: Session, runs: list[WorkflowRun]) -> None:
    overview = get_generation_queue_overview(session)
    queued_positions = get_queued_generation_positions(session)
    for run in runs:
        run.__dict__["_queue_metadata"] = get_workflow_run_queue_metadata(
            session,
            run,
            overview=overview,
            queued_positions=queued_positions,
        )


def get_active_workflow_status(session: Session, product_id: str) -> ProductWorkflowStatusSnapshot:
    workflow = session.scalar(
        workflow_status_query().where(ProductWorkflow.product_id == product_id, ProductWorkflow.active.is_(True))
    )
    if workflow is None:
        get_product_or_raise(session, product_id)
        raise NotFoundError("Workflow not found")
    nodes = list(
        session.scalars(
            select(WorkflowNode)
            .options(
                load_only(
                    WorkflowNode.id,
                    WorkflowNode.workflow_id,
                    WorkflowNode.status,
                    WorkflowNode.failure_reason,
                    WorkflowNode.last_run_at,
                    WorkflowNode.updated_at,
                )
            )
            .where(WorkflowNode.workflow_id == workflow.id)
            .order_by(WorkflowNode.position_x, WorkflowNode.position_y, WorkflowNode.created_at)
        )
    )
    runs = list(
        session.scalars(
            select(WorkflowRun)
            .options(
                load_only(
                    WorkflowRun.id,
                    WorkflowRun.workflow_id,
                    WorkflowRun.status,
                    WorkflowRun.started_at,
                    WorkflowRun.finished_at,
                    WorkflowRun.failure_reason,
                    WorkflowRun.is_retryable,
                    WorkflowRun.progress_metadata,
                ),
                selectinload(WorkflowRun.node_runs).load_only(
                    WorkflowNodeRun.id,
                    WorkflowNodeRun.workflow_run_id,
                    WorkflowNodeRun.node_id,
                    WorkflowNodeRun.status,
                    WorkflowNodeRun.failure_reason,
                    WorkflowNodeRun.started_at,
                    WorkflowNodeRun.finished_at,
                ),
            )
            .where(WorkflowRun.workflow_id == workflow.id)
            .order_by(desc(WorkflowRun.started_at), desc(WorkflowRun.id))
            .limit(10)
        )
    )
    node_context_runs = list(
        session.scalars(
            select(WorkflowRun)
            .options(
                load_only(
                    WorkflowRun.id,
                    WorkflowRun.workflow_id,
                    WorkflowRun.status,
                    WorkflowRun.started_at,
                    WorkflowRun.finished_at,
                    WorkflowRun.failure_reason,
                    WorkflowRun.is_retryable,
                    WorkflowRun.progress_metadata,
                ),
                selectinload(WorkflowRun.node_runs).load_only(
                    WorkflowNodeRun.id,
                    WorkflowNodeRun.workflow_run_id,
                    WorkflowNodeRun.node_id,
                    WorkflowNodeRun.status,
                    WorkflowNodeRun.failure_reason,
                    WorkflowNodeRun.started_at,
                    WorkflowNodeRun.finished_at,
                ),
            )
            .where(WorkflowRun.workflow_id == workflow.id)
            .order_by(desc(WorkflowRun.started_at), desc(WorkflowRun.id))
        )
    )
    attach_workflow_run_queue_metadata(session, runs)
    return ProductWorkflowStatusSnapshot(
        workflow=workflow,
        nodes=nodes,
        runs=runs,
        node_context_runs=node_context_runs,
    )


def get_node_or_raise(session: Session, node_id: str) -> WorkflowNode:
    node = session.get(WorkflowNode, node_id)
    if node is None:
        raise NotFoundError("Workflow node not found")
    return node


def get_edge_or_raise(session: Session, edge_id: str) -> WorkflowEdge:
    edge = session.get(WorkflowEdge, edge_id)
    if edge is None:
        raise NotFoundError("Workflow edge not found")
    return edge


def default_node_specs(product: Product) -> list[dict[str, Any]]:
    return [
        {
            "key": "context",
            "node_type": WorkflowNodeType.PRODUCT_CONTEXT,
            "title": "Product",
            "position_x": 40,
            "position_y": 120,
            "config_json": {},
        },
        {
            "key": "copy",
            "node_type": WorkflowNodeType.COPY_GENERATION,
            "title": "Copy",
            "position_x": 320,
            "position_y": 80,
            "config_json": {"instruction": f"Generate copy suitable for the product image around {product.name}"},
        },
        {
            "key": "image",
            "node_type": WorkflowNodeType.IMAGE_GENERATION,
            "title": "image generation",
            "position_x": 620,
            "position_y": 100,
            "config_json": {
                "instruction": "Generate a product image from the product and copy",
                "size": DEFAULT_IMAGE_SIZE,
            },
        },
        {
            "key": "reference",
            "node_type": WorkflowNodeType.REFERENCE_IMAGE,
            "title": "Reference image",
            "position_x": 920,
            "position_y": 120,
            "config_json": {"role": "reference", "label": "Generated result slot"},
        },
    ]


def default_edges(nodes_by_key: dict[str, WorkflowNode], workflow_id: str) -> list[WorkflowEdge]:
    pairs = [
        ("context", "copy"),
        ("context", "image"),
        ("copy", "image"),
        ("image", "reference"),
    ]
    return [
        WorkflowEdge(
            workflow_id=workflow_id,
            source_node_id=nodes_by_key[source].id,
            target_node_id=nodes_by_key[target].id,
            source_handle="output",
            target_handle="input",
        )
        for source, target in pairs
    ]


def default_title_for_type(node_type: WorkflowNodeType) -> str:
    return {
        WorkflowNodeType.PRODUCT_CONTEXT: "Product",
        WorkflowNodeType.REFERENCE_IMAGE: "Reference image",
        WorkflowNodeType.COPY_GENERATION: "Copy",
        WorkflowNodeType.IMAGE_GENERATION: "image generation",
    }[node_type]


def topological_nodes(workflow: ProductWorkflow) -> list[WorkflowNode]:
    nodes = {node.id: node for node in workflow.nodes}
    ordered_ids = topological_node_ids(
        [
            WorkflowRuleNode(
                id=node.id,
                node_type=node.node_type,
                position_x=node.position_x,
                config_json=node.config_json,
            )
            for node in workflow.nodes
        ],
        [
            WorkflowRuleEdge(source_node_id=edge.source_node_id, target_node_id=edge.target_node_id)
            for edge in workflow.edges
        ],
    )
    return [nodes[node_id] for node_id in ordered_ids]


def latest_workflow_runs(workflow: ProductWorkflow, limit: int = 10) -> list[WorkflowRun]:
    return sorted(
        workflow.runs,
        key=lambda item: (
            item.started_at,
            item.status == WorkflowRunStatus.RUNNING,
            item.finished_at or item.started_at,
            item.id,
        ),
        reverse=True,
    )[:limit]
