"""Function tools that back the SeftFlow Copilot agent and MCP server."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from productflow_backend.agent.guards import (
    GuardError,
    TurnBudget,
    check_prompt_injection,
    redis_rate_limit,
)
from productflow_backend.application import gallery as gallery_uc
from productflow_backend.application import image_sessions as image_sessions_uc
from productflow_backend.application import use_cases
from productflow_backend.infrastructure.db.session import get_session_factory


def _open_session():
    factory = get_session_factory()
    return factory()


def _product_summary(product: Any) -> dict[str, Any]:
    return {
        "id": product.id,
        "name": product.name,
        "category": getattr(product, "category", None),
        "price": getattr(product, "price", None),
        "source_note": getattr(product, "source_note", None),
        "workflow_state": getattr(getattr(product, "workflow_state", None), "value", None)
        or getattr(product, "workflow_state", None),
    }


class SeftFlowTools:
    """Tool set bound to a caller session (for rate-limit and quota tracking)."""

    def __init__(self, *, session_id: str, budget: TurnBudget | None = None) -> None:
        self._session_id = session_id
        self._budget = budget or TurnBudget()

    def _spend(self) -> None:
        redis_rate_limit(self._session_id)
        self._budget.spend_tool_call()

    def list_products(self, limit: int = 20, page: int = 1) -> dict[str, Any]:
        """List products (paginated, most recent first)."""
        self._spend()
        with _open_session() as db:
            products, total = use_cases.list_products(
                db,
                status=None,
                page=max(1, int(page)),
                page_size=max(1, min(int(limit), 100)),
            )
            return {
                "total": total,
                "items": [_product_summary(p) for p in products],
            }

    def get_workflow_status(self, product_id: str) -> dict[str, Any]:
        """Get product history summary (copy sets, poster variants)."""
        self._spend()
        check_prompt_injection(product_id)
        with _open_session() as db:
            history = use_cases.get_product_history(db, product_id)
            return {
                "product_id": product_id,
                "copy_sets": [
                    {"id": cs.id, "created_at": cs.created_at.isoformat()}
                    for cs in history["copy_sets"]
                ],
                "poster_variants": [
                    {"id": pv.id, "created_at": pv.created_at.isoformat()}
                    for pv in history["poster_variants"]
                ],
            }

    def create_product(
        self,
        name: str,
        category: str | None = None,
        price: str | None = None,
        source_note: str | None = None,
    ) -> dict[str, Any]:
        """Create a text-only product record. Image uploads are handled by the web UI."""
        self._spend()
        check_prompt_injection(name, category or "", price or "", source_note or "")
        if not name.strip():
            raise GuardError("Product name cannot be empty")
        with _open_session() as db:
            product = use_cases.create_product(
                db,
                name=name.strip(),
                category=category,
                price=price,
                source_note=source_note,
                image_bytes=b"",
                filename="agent-placeholder.png",
                content_type="image/png",
                reference_image_uploads=None,
                canvas_template_key=None,
            )
            return {"created": True, "product": _product_summary(product)}

    def generate_copy(
        self,
        product_id: str,
        style: str | None = None,
        instruction: str | None = None,
    ) -> dict[str, Any]:
        """Return latest copy set for a product. Regeneration is driven by the workflow route."""
        self._spend()
        check_prompt_injection(product_id, style or "", instruction or "")
        with _open_session() as db:
            history = use_cases.get_product_history(db, product_id)
            latest = history["copy_sets"][0] if history["copy_sets"] else None
            return {
                "product_id": product_id,
                "style": style,
                "instruction": instruction,
                "latest_copy_set_id": latest.id if latest else None,
                "hint": (
                    "Copy generation is driven by the workflow engine. "
                    "Open the product workspace or call `run_product_workflow` to regenerate."
                ),
            }

    def generate_image(
        self,
        prompt: str,
        product_id: str | None = None,
        size: str = "1024x1024",
        n: int = 1,
        image_session_id: str | None = None,
    ) -> dict[str, Any]:
        """Submit an image-generation request. Creates a session if needed."""
        self._spend()
        check_prompt_injection(prompt, product_id or "", size, image_session_id or "")
        n = max(1, min(int(n), 4))
        with _open_session() as db:
            if not image_session_id:
                created = image_sessions_uc.create_image_session(
                    db, product_id=product_id, title=prompt[:60] or None
                )
                image_session_id = created.id
            image_session = image_sessions_uc.submit_image_session_generation_task(
                db,
                image_session_id=image_session_id,
                prompt=prompt,
                size=size,
                generation_count=n,
            )
            latest_task_id = None
            if image_session.generation_tasks:
                latest_task_id = image_session.generation_tasks[-1].id
            return {
                "image_session_id": image_session_id,
                "generation_task_id": latest_task_id,
                "prompt": prompt,
                "size": size,
                "count": n,
            }

    def add_to_gallery(self, image_session_asset_id: str) -> dict[str, Any]:
        """Persist a generated asset into the gallery."""
        self._spend()
        check_prompt_injection(image_session_asset_id)
        with _open_session() as db:
            result = gallery_uc.save_generated_asset_to_gallery(
                db, image_session_asset_id=image_session_asset_id
            )
            return {
                "saved": True,
                "gallery_entry_id": getattr(result, "gallery_entry_id", None)
                or getattr(getattr(result, "entry", None), "id", None),
            }

    def run_product_workflow(self, product_id: str) -> dict[str, Any]:
        """Return current workflow status. The full trigger requires the workflow HTTP route."""
        self._spend()
        check_prompt_injection(product_id)
        return self.get_workflow_status(product_id) | {
            "hint": "Trigger the workflow from the product workbench UI or POST /api/products/{id}/workflow/run.",
        }


def build_tool_map(tools: SeftFlowTools) -> dict[str, Callable[..., Any]]:
    """Return a name-indexed mapping used by the MCP server and ADK bindings."""
    return {
        "list_products": tools.list_products,
        "get_workflow_status": tools.get_workflow_status,
        "create_product": tools.create_product,
        "generate_copy": tools.generate_copy,
        "generate_image": tools.generate_image,
        "add_to_gallery": tools.add_to_gallery,
        "run_product_workflow": tools.run_product_workflow,
    }
