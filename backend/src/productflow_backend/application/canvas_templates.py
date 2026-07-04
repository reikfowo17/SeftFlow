from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from productflow_backend.domain.enums import WorkflowNodeType
from productflow_backend.domain.errors import BusinessValidationError
from productflow_backend.domain.workflow_rules import WorkflowRuleEdge, WorkflowRuleNode, topological_node_ids

TemplateKind = Literal["full_canvas", "node_group"]
SUPPORTED_CANVAS_TEMPLATE_NODE_TYPES = frozenset(
    {
        WorkflowNodeType.PRODUCT_CONTEXT,
        WorkflowNodeType.REFERENCE_IMAGE,
        WorkflowNodeType.COPY_GENERATION,
        WorkflowNodeType.IMAGE_GENERATION,
    }
)
FULL_CANVAS_TEMPLATE_COLUMN_GAP = 380


class CanvasTemplateScenario(StrEnum):
    MAIN_IMAGE = "main_image"
    TAOBAO_MAIN_IMAGE = "taobao_main_image"
    XIAOHONGSHU_IMAGE = "xiaohongshu_image"
    MULTI_ANGLE = "multi_angle"
    SKU_VARIANT = "sku_variant"
    FEATURE_INFOGRAPHIC = "feature_infographic"
    SIZE_SPEC = "size_spec"
    SCALE_REFERENCE = "scale_reference"
    PACKAGE_CHECKLIST = "package_checklist"
    USAGE_STEPS = "usage_steps"
    COMPARISON = "comparison"
    MODEL_LIFESTYLE = "model_lifestyle"
    SCENE_IMAGE = "scene_image"
    DETAIL_MATERIAL = "detail_material"
    CAMPAIGN_PROMOTION = "campaign_promotion"
    SHORT_VIDEO_COVER = "short_video_cover"
    WHITE_BACKGROUND = "white_background"


class CanvasTemplateScenarioMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario: CanvasTemplateScenario
    title: str
    description: str
    ecommerce_stage: str
    tags: tuple[str, ...] = ()


class CanvasTemplateReferenceInputHint(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_key: str
    role: str
    label: str
    required: bool = False
    description: str


class CanvasTemplateOutputSlot(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_key: str
    label: str
    description: str


class CanvasTemplateSuggestedConnection(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_node_key: str
    target_node_key: str
    reason: str


class CanvasTemplateDefaultExternalConnection(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: Literal["existing_product_context"]
    target_node_key: str
    label: str
    reason: str


class CanvasTemplateNodeSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    node_type: WorkflowNodeType
    title: str
    position_x: int = 0
    position_y: int = 0
    config_json: dict[str, Any] = Field(default_factory=dict)
    prompt_seed: str | None = None
    instruction_seed: str | None = None
    size: str | None = None
    output_slot_label: str | None = None
    reference_input_hint: str | None = None

    @field_validator("config_json")
    @classmethod
    def validate_config_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        return dict(value)


class CanvasTemplateEdgeSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_node_key: str
    target_node_key: str
    source_handle: str | None = "output"
    target_handle: str | None = "input"


class CanvasTemplate(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    version: int = 1
    kind: TemplateKind
    title: str
    description: str
    source: Literal["builtin", "user"] = "builtin"
    user_template_id: str | None = None
    scenario: CanvasTemplateScenarioMetadata
    nodes: tuple[CanvasTemplateNodeSpec, ...]
    edges: tuple[CanvasTemplateEdgeSpec, ...] = ()
    prompt_seeds: tuple[str, ...] = ()
    instruction_seeds: tuple[str, ...] = ()
    output_slots: tuple[CanvasTemplateOutputSlot, ...] = ()
    reference_input_hints: tuple[CanvasTemplateReferenceInputHint, ...] = ()
    suggested_connections: tuple[CanvasTemplateSuggestedConnection, ...] = ()
    default_external_connections: tuple[CanvasTemplateDefaultExternalConnection, ...] = ()

    @model_validator(mode="after")
    def validate_contract(self) -> CanvasTemplate:
        validate_canvas_template(self)
        return self


def validate_canvas_template(template: CanvasTemplate) -> None:
    if template.version != 1:
        raise BusinessValidationError("The canvas template version must be v1")
    if template.kind not in ("full_canvas", "node_group"):
        raise BusinessValidationError("The canvas template type is not supported.")
    if not template.nodes:
        raise BusinessValidationError("The canvas template must contain at least one node.")

    nodes_by_key: dict[str, CanvasTemplateNodeSpec] = {}
    for node in template.nodes:
        if node.key in nodes_by_key:
            raise BusinessValidationError("Canvas template node keys must be unique")
        nodes_by_key[node.key] = node
        if node.node_type not in SUPPORTED_CANVAS_TEMPLATE_NODE_TYPES:
            raise BusinessValidationError("The canvas template contains unsupported node types.")
        if template.kind == "node_group" and node.node_type == WorkflowNodeType.PRODUCT_CONTEXT:
            raise BusinessValidationError("A node group template cannot contain product information nodes.")
        if node.size is not None and node.node_type != WorkflowNodeType.IMAGE_GENERATION:
            raise BusinessValidationError("Only image generation nodes can specify dimensions.")

    for edge in template.edges:
        if edge.source_node_key == edge.target_node_key:
            raise BusinessValidationError("Canvas template connections cannot connect to themselves.")
        if edge.source_node_key not in nodes_by_key or edge.target_node_key not in nodes_by_key:
            raise BusinessValidationError("A canvas template connection references a non-existent node.")

    _validate_node_reference_items(
        template.output_slots,
        nodes_by_key=nodes_by_key,
        expected_type=WorkflowNodeType.REFERENCE_IMAGE,
        item_name="output slot",
    )
    _validate_node_reference_items(
        template.reference_input_hints,
        nodes_by_key=nodes_by_key,
        expected_type=WorkflowNodeType.REFERENCE_IMAGE,
        item_name="reference input hint",
    )
    for connection in template.suggested_connections:
        if connection.source_node_key == connection.target_node_key:
            raise BusinessValidationError("Canvas template connection suggestion must not connect a node to itself")
        if (
            connection.source_node_key not in nodes_by_key
            or connection.target_node_key not in nodes_by_key
        ):
            raise BusinessValidationError("Canvas template connection suggestion references a non-existent node")
    for connection in template.default_external_connections:
        if template.kind != "node_group":
            raise BusinessValidationError("Only node-group templates can declare default external connections")
        if connection.target_node_key not in nodes_by_key:
            raise BusinessValidationError("Canvas template default external connection references a non-existent node")
        if nodes_by_key[connection.target_node_key].node_type not in {
            WorkflowNodeType.COPY_GENERATION,
            WorkflowNodeType.IMAGE_GENERATION,
        }:
            raise BusinessValidationError("Canvas template default external connection can only target copy or image-generation nodes")

    try:
        topological_node_ids(
            [
                WorkflowRuleNode(
                    id=node.key,
                    node_type=node.node_type,
                    position_x=node.position_x,
                    config_json=node.config_json,
                )
                for node in template.nodes
            ],
            [
                WorkflowRuleEdge(
                    source_node_id=edge.source_node_key,
                    target_node_id=edge.target_node_key,
                )
                for edge in template.edges
            ],
        )
    except BusinessValidationError:
        raise
    except ValueError as exc:
        raise BusinessValidationError(str(exc)) from exc


def list_builtin_canvas_templates() -> tuple[CanvasTemplate, ...]:
    return BUILTIN_CANVAS_TEMPLATES


def get_builtin_canvas_template(template_key: str) -> CanvasTemplate:
    for template in BUILTIN_CANVAS_TEMPLATES:
        if template.key == template_key:
            return template
    raise BusinessValidationError("Canvas template not found")


def _validate_node_reference_items(
    items: tuple[CanvasTemplateOutputSlot, ...] | tuple[CanvasTemplateReferenceInputHint, ...],
    *,
    nodes_by_key: dict[str, CanvasTemplateNodeSpec],
    expected_type: WorkflowNodeType,
    item_name: str,
) -> None:
    for item in items:
        node = nodes_by_key.get(item.node_key)
        if node is None:
            raise BusinessValidationError(f"Canvas template {item_name} references a non-existent node")
        if node.node_type != expected_type:
            raise BusinessValidationError(f"Canvas template {item_name} must reference a reference-image node")


def _scenario(
    scenario: CanvasTemplateScenario,
    *,
    title: str,
    description: str,
    ecommerce_stage: str,
    tags: tuple[str, ...],
) -> CanvasTemplateScenarioMetadata:
    return CanvasTemplateScenarioMetadata(
        scenario=scenario,
        title=title,
        description=description,
        ecommerce_stage=ecommerce_stage,
        tags=tags,
    )


def _node(
    key: str,
    node_type: WorkflowNodeType,
    *,
    title: str,
    x: int,
    y: int,
    config_json: dict[str, Any] | None = None,
    prompt_seed: str | None = None,
    instruction_seed: str | None = None,
    size: str | None = None,
    output_slot_label: str | None = None,
    reference_input_hint: str | None = None,
) -> CanvasTemplateNodeSpec:
    config = dict(config_json or {})
    if instruction_seed is not None and "instruction" not in config:
        config["instruction"] = instruction_seed
    if node_type == WorkflowNodeType.COPY_GENERATION:
        config = _copy_node_config(config, instruction_seed=instruction_seed)
    if size is not None and "size" not in config:
        config["size"] = size
    return CanvasTemplateNodeSpec(
        key=key,
        node_type=node_type,
        title=title,
        position_x=x,
        position_y=y,
        config_json=config,
        prompt_seed=prompt_seed,
        instruction_seed=instruction_seed,
        size=size,
        output_slot_label=output_slot_label,
        reference_input_hint=reference_input_hint,
    )


def _copy_node_config(config: dict[str, Any], *, instruction_seed: str | None) -> dict[str, Any]:
    instruction = str(config.get("instruction") or instruction_seed or "")
    output_mode = config.get("output_mode")
    if output_mode not in {"freeform", "blocks", "layout_brief"}:
        output_mode = _infer_copy_output_mode(instruction)
    next_config = {
        **config,
        "version": 2,
        "instruction": instruction,
        "output_mode": output_mode,
    }
    next_config.setdefault("purpose", _infer_copy_purpose(instruction))
    next_config.setdefault("requested_slots", [])
    return next_config


def _infer_copy_output_mode(instruction: str) -> str:
    if any(keyword in instruction for keyword in ("hierarchy", "layout", "whitespace", "composition", "infographic", "visual")):
        return "layout_brief"
    if any(keyword in instruction for keyword in ("Selling points", "Specifications", "size", "Steps", "Checklist", "Comparison", "Tag", "parameters")):
        return "blocks"
    return "freeform"


def _infer_copy_purpose(instruction: str) -> str:
    mapping = (
        ("white background", "white_background"),
        ("short video", "short_video_cover"),
        ("campaign", "campaign_promotion"),
        ("promotion", "campaign_promotion"),
        ("Comparison", "comparison"),
        ("Steps", "usage_steps"),
        ("Checklist", "package_checklist"),
        ("packaging", "package_checklist"),
        ("scale", "scale_reference"),
        ("size", "size_spec"),
        ("Specifications", "size_spec"),
        ("Selling points", "feature_infographic"),
        ("SKU", "sku_variant"),
        ("scene", "scene_image"),
        ("cover", "content_cover"),
    )
    for keyword, purpose in mapping:
        if keyword in instruction:
            return purpose
    return "ecommerce_copy"


def _edge(source: str, target: str) -> CanvasTemplateEdgeSpec:
    return CanvasTemplateEdgeSpec(source_node_key=source, target_node_key=target)


def _output_slot(node_key: str, label: str, description: str) -> CanvasTemplateOutputSlot:
    return CanvasTemplateOutputSlot(node_key=node_key, label=label, description=description)


def _reference_hint(
    node_key: str,
    *,
    role: str,
    label: str,
    description: str,
    required: bool = False,
) -> CanvasTemplateReferenceInputHint:
    return CanvasTemplateReferenceInputHint(
        node_key=node_key,
        role=role,
        label=label,
        required=required,
        description=description,
    )


def _suggest(source: str, target: str, reason: str) -> CanvasTemplateSuggestedConnection:
    return CanvasTemplateSuggestedConnection(source_node_key=source, target_node_key=target, reason=reason)


def _default_product_connection(target: str) -> CanvasTemplateDefaultExternalConnection:
    return CanvasTemplateDefaultExternalConnection(
        source="existing_product_context",
        target_node_key=target,
        label="Auto-connect product",
        reason="Reuses the current canvas's product info and product main image.",
    )


def _instruction_seeds(nodes: tuple[CanvasTemplateNodeSpec, ...]) -> tuple[str, ...]:
    return tuple(node.instruction_seed for node in nodes if node.instruction_seed)


def _spread_full_canvas_columns(nodes: tuple[CanvasTemplateNodeSpec, ...]) -> tuple[CanvasTemplateNodeSpec, ...]:
    # Full canvas templates use larger horizontal spacing so the create-page preview does not look like a node-group template.
    sorted_columns = sorted({node.position_x for node in nodes})
    if len(sorted_columns) <= 1:
        return nodes
    origin_x = sorted_columns[0]
    column_positions = {
        column_x: origin_x + index * FULL_CANVAS_TEMPLATE_COLUMN_GAP
        for index, column_x in enumerate(sorted_columns)
    }
    return tuple(node.model_copy(update={"position_x": column_positions[node.position_x]}) for node in nodes)


def _full_canvas_template(
    *,
    key: str,
    title: str,
    description: str,
    scenario: CanvasTemplateScenarioMetadata,
    nodes: tuple[CanvasTemplateNodeSpec, ...],
    edges: tuple[tuple[str, str], ...],
    output_slots: tuple[CanvasTemplateOutputSlot, ...],
    reference_input_hints: tuple[CanvasTemplateReferenceInputHint, ...] = (),
    suggested_connections: tuple[CanvasTemplateSuggestedConnection, ...] = (),
) -> CanvasTemplate:
    spaced_nodes = _spread_full_canvas_columns(nodes)
    seeds = _instruction_seeds(spaced_nodes)
    return CanvasTemplate(
        key=key,
        kind="full_canvas",
        title=title,
        description=description,
        scenario=scenario,
        nodes=spaced_nodes,
        edges=tuple(_edge(source, target) for source, target in edges),
        prompt_seeds=seeds,
        instruction_seeds=seeds,
        output_slots=output_slots,
        reference_input_hints=reference_input_hints,
        suggested_connections=suggested_connections,
    )


def _node_group_template(
    *,
    key: str,
    title: str,
    description: str,
    scenario: CanvasTemplateScenarioMetadata,
    copy_instruction: str,
    image_instruction: str,
    size: str,
    output_label: str,
    output_description: str,
    reference_label: str,
    reference_description: str,
    reference_role: str,
) -> CanvasTemplate:
    reference_x = 0
    reference_y = 0
    copy_x = 440
    copy_y = 40
    image_x = 880
    image_y = 120
    output_x = 1320
    output_y = 120
    nodes = (
        _node(
            "reference",
            WorkflowNodeType.REFERENCE_IMAGE,
            title=reference_label,
            x=reference_x,
            y=reference_y,
            config_json={"role": reference_role, "label": reference_label},
            reference_input_hint=reference_description,
        ),
        _node(
            "copy",
            WorkflowNodeType.COPY_GENERATION,
            title=title,
            x=copy_x,
            y=copy_y,
            instruction_seed=copy_instruction,
        ),
        _node(
            "image",
            WorkflowNodeType.IMAGE_GENERATION,
            title=f"Generate {output_label}",
            x=image_x,
            y=image_y,
            instruction_seed=image_instruction,
            size=size,
        ),
        _node(
            "output",
            WorkflowNodeType.REFERENCE_IMAGE,
            title=output_label,
            x=output_x,
            y=output_y,
            config_json={"role": "output", "label": output_label},
            output_slot_label=output_label,
        ),
    )
    return CanvasTemplate(
        key=key,
        kind="node_group",
        title=title,
        description=description,
        scenario=scenario,
        nodes=nodes,
        edges=(
            _edge("reference", "copy"),
            _edge("reference", "image"),
            _edge("copy", "image"),
            _edge("image", "output"),
        ),
        prompt_seeds=(copy_instruction, image_instruction),
        instruction_seeds=(copy_instruction, image_instruction),
        output_slots=(_output_slot("output", output_label, output_description),),
        reference_input_hints=(
            _reference_hint(
                "reference",
                role=reference_role,
                label=reference_label,
                description=reference_description,
            ),
        ),
        suggested_connections=(
            _suggest("reference", "copy", "Reference images provide specs, materials, or subject constraints for the copy."),
            _suggest("reference", "image", "Reference images can serve as image-generation inputs to keep the subject and details consistent."),
            _suggest("copy", "image", "Copy provides titles, selling points, and visual focus for image generation."),
            _suggest("image", "output", "Image-generation results are written into the downstream reference-image output slot."),
        ),
        default_external_connections=(
            _default_product_connection("copy"),
            _default_product_connection("image"),
        ),
    )


_MAIN_IMAGE_COPY = "Extract the product's core selling points; the copy should suit an e-commerce main image with a concise headline and three benefit points."
_MAIN_IMAGE_INSTRUCTION = "Generate a clean e-commerce main image focused on the product subject; keep the subject clear so the selling points are visually expressed."

_TAOBAO_COPY = (
    "Extract the core selling points suitable for a Taobao main image, highlighting the subject, target audience, and reasons to buy; keep it to a short headline and three benefit points."
)
_TAOBAO_IMAGE = "Generate a Taobao main image with 1:1 composition, the subject clearly centered, a clean background, and visually clear selling points; avoid stacking complex text."

_XHS_COPY = "Extract a content angle for a Xiaohongshu note cover that highlights real usage experience, atmosphere, and authenticity; avoid hard-sell tone."
_XHS_IMAGE = "Generate a Xiaohongshu-style vertical image: natural, lifestyle-feeling, with the product clearly visible; suitable for note covers and recommendation content."

_MULTI_ANGLE_COPY = "Plan a multi-angle display order for the product covering front, side, back, or key structures; keep descriptions short and explicit."
_MULTI_ANGLE_IMAGE = "Generate multi-angle display images of the same product with consistent subject proportions and clear angles; suitable for detail-page carousels and platform galleries."

_SKU_COPY = "Generate short explanations around the current SKU's color, specs, capacity, or combination differences, highlighting what users most need when comparing."
_SKU_IMAGE = "Generate product images that emphasize SKU differences while keeping the subject consistent and clearly showing specs, color, or combination differences."

_FEATURE_INFOGRAPHIC_COPY = "Extract 3 to 5 functional selling points with the most influence on purchase decisions and provide a short tag and visual suggestion for each."
_FEATURE_INFOGRAPHIC_IMAGE = "Generate a product-feature benefit infographic with a clear subject and a hierarchical layout of selling points; suitable for detail-page above-the-fold persuasion."

_SIZE_SPEC_COPY = "Summarize product size, capacity, specs, material parameters, and caution notes; output structured short copy suitable for a spec diagram."
_SIZE_SPEC_IMAGE = "Generate a size/spec diagram preserving the product outline and key annotation areas with a clear layout; suitable for detail-page parameter explanation."

_SCALE_REFERENCE_COPY = "Extract real product scale, handheld/worn/desktop references, and usage distance; avoid exaggerating proportions."
_SCALE_REFERENCE_IMAGE = "Generate a scale display image with real reference objects so users intuitively understand the product's size, thickness, or capacity."

_PACKAGE_COPY = "Summarize package contents, accessory checklist, freebies, and unboxing state; form short tags suitable for a checklist image."
_PACKAGE_IMAGE = "Generate a packaging/checklist flat-lay image clearly showing the product, box, accessories, and counts; suitable for detail-page unboxing explanation."

_USAGE_STEPS_COPY = "Break down installation, unboxing, wearing, cleaning, or usage steps into 3 to 4 steps with one short sentence each."
_USAGE_STEPS_IMAGE = "Generate a usage-steps diagram with a clear step order, authentic actions, and recognizable product and key parts."

_COMPARISON_COPY = "Extract comparison dimensions suitable for clear contrasts with old versions, competitors, standard models, or different bundles; avoid unverifiable absolute claims."
_COMPARISON_IMAGE = "Generate a product comparison image using a left/right or top/bottom structure to show differences, highlighting verifiable spec, function, or experience differences."

_LIFESTYLE_COPY = "Extract target audience, usage scenarios, and lifestyle atmosphere; avoid exaggerated promises."
_LIFESTYLE_IMAGE = "Generate a natural lifestyle image where the product is clearly visible in a real usage scenario; people or environment should serve the product."

_SCENE_COPY = "Extract suitable usage scenarios, matching objects, and environment keywords for the product."
_SCENE_IMAGE = "Generate a product usage-scenario image with a believable environment while keeping the product as the main subject."

_DETAIL_COPY = "Extract product material, craftsmanship, structure, and functional details into short titles and bullet points."
_DETAIL_IMAGE = "Generate a detail or material close-up image emphasising texture, structure, and functional points; avoid distortion."

_CAMPAIGN_COPY = "Generate campaign-image copy that highlights promotion, time-sensitivity, and product benefits with a clear tone and no overpromising."
_CAMPAIGN_IMAGE = "Generate a campaign promotional product image preserving the product subject while adding a promotional atmosphere and clear visual hierarchy."

_SHORT_VIDEO_COVER_COPY = "Extract one strong hook and two supporting points for a short-video cover; keep the tone direct without clickbait."
_SHORT_VIDEO_COVER_IMAGE = "Generate a vertical short-video cover with a striking subject and clear cover hook; suitable for in-app short videos and content placement."

_WHITE_BACKGROUND_COPY = "Extract the product subject, angle, and spec focus to preserve in a white-background image; do not add promotional copy."
_WHITE_BACKGROUND_IMAGE = "Generate a white-background product image with a clean backdrop, sharp product edges, and authentic proportions and materials."


BUILTIN_CANVAS_TEMPLATES: tuple[CanvasTemplate, ...] = (
    _full_canvas_template(
        key="ecommerce-main-image-v1",
        title="E-commerce main image",
        description="Generate a product hero image with a clear subject, benefits, and composition.",
        scenario=_scenario(
            CanvasTemplateScenario.MAIN_IMAGE,
            title="Main image",
            description="For product listings and the first screen of the detail page.",
            ecommerce_stage="listing",
            tags=("main-image", "hero", "listing"),
        ),
        nodes=(
            _node("product", WorkflowNodeType.PRODUCT_CONTEXT, title="Product info", x=48, y=132),
            _node(
                "copy",
                WorkflowNodeType.COPY_GENERATION,
                title="Main-image selling points",
                x=320,
                y=88,
                instruction_seed=_MAIN_IMAGE_COPY,
            ),
            _node(
                "image",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Generate Main image",
                x=640,
                y=96,
                instruction_seed=_MAIN_IMAGE_INSTRUCTION,
                size="1024x1024",
            ),
            _node(
                "output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Main image output",
                x=960,
                y=72,
                config_json={"role": "output", "label": "Main image output"},
                output_slot_label="Main image output",
            ),
            _node(
                "refine",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Refine main image",
                x=1280,
                y=180,
                instruction_seed="continue refining the subject, lighting, and composition based on the main-image output; generate a comparable new version.",
                size="1024x1024",
            ),
            _node(
                "refined_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Refined output",
                x=1600,
                y=180,
                config_json={"role": "output", "label": "Refined output"},
                output_slot_label="Refined output",
            ),
        ),
        edges=(
            ("product", "copy"),
            ("product", "image"),
            ("copy", "image"),
            ("image", "output"),
            ("output", "refine"),
            ("refine", "refined_output"),
        ),
        output_slots=(
            _output_slot("output", "Main image output", "Candidates for product listings and detail-page hero images."),
            _output_slot("refined_output", "Refined output", "Refined main-image candidates."),
        ),
        suggested_connections=(
            _suggest("product", "copy", "Product info provides the base context for main-image selling points."),
            _suggest("copy", "image", "Main-image selling points feed into the image-generation node and constrain the visual focus."),
            _suggest("output", "refine", "The main-image output is used downstream as a reference to generate comparable refined versions."),
        ),
    ),
    _full_canvas_template(
        key="ecommerce-taobao-main-image-v1",
        title="Taobao main image",
        description="Generate a 1:1 product main image suitable for Taobao search, recommendations, and detail-page above-the-fold.",
        scenario=_scenario(
            CanvasTemplateScenario.TAOBAO_MAIN_IMAGE,
            title="Taobao main image",
            description="For Taobao listing traffic and detail-page above-the-fold; emphasises subject clarity and clear selling points.",
            ecommerce_stage="listing",
            tags=("taobao", "main-image", "marketplace"),
        ),
        nodes=(
            _node("product", WorkflowNodeType.PRODUCT_CONTEXT, title="Product info", x=48, y=132),
            _node(
                "angle",
                WorkflowNodeType.COPY_GENERATION,
                title="Search benefits",
                x=320,
                y=72,
                instruction_seed=_TAOBAO_COPY,
            ),
            _node(
                "main",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Main image version",
                x=640,
                y=72,
                instruction_seed=_TAOBAO_IMAGE,
                size="1024x1024",
            ),
            _node(
                "main_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Taobao main image",
                x=960,
                y=72,
                config_json={"role": "output", "label": "Taobao main image"},
                output_slot_label="Taobao main image",
            ),
            _node(
                "clean",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Clean version",
                x=640,
                y=208,
                instruction_seed="Generate a more restrained Taobao main image with fewer decorative elements while keeping the subject and edges sharp.",
                size="1024x1024",
            ),
            _node(
                "clean_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Clean main image",
                x=960,
                y=208,
                config_json={"role": "output", "label": "Clean main image"},
                output_slot_label="Clean main image",
            ),
        ),
        edges=(
            ("product", "angle"),
            ("product", "main"),
            ("angle", "main"),
            ("main", "main_output"),
            ("product", "clean"),
            ("angle", "clean"),
            ("clean", "clean_output"),
        ),
        output_slots=(
            _output_slot("main_output", "Taobao main image", "Main-image candidates for Taobao search listings, recommendation streams, and detail-page above-the-fold."),
            _output_slot("clean_output", "Clean main image", "A more restrained main-image backup version."),
        ),
        suggested_connections=(
            _suggest("angle", "main", "Search benefits provide the conversion focus for the main-image version."),
            _suggest("angle", "clean", "Generate a cleaner backup main image from the same selling points."),
        ),
    ),
    _full_canvas_template(
        key="ecommerce-xiaohongshu-image-v1",
        title="Xiaohongshu image",
        description="Generate a vertical lifestyle image suitable for Xiaohongshu note covers and recommendation content.",
        scenario=_scenario(
            CanvasTemplateScenario.XIAOHONGSHU_IMAGE,
            title="Xiaohongshu",
            description="For Xiaohongshu note covers, recommendation content, and lifestyle showcases.",
            ecommerce_stage="content",
            tags=("xiaohongshu", "cover", "lifestyle"),
        ),
        nodes=(
            _node(
                "style_reference",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Note style reference",
                x=48,
                y=48,
                config_json={"role": "style", "label": "Note style reference"},
                reference_input_hint="Upload reference images for the target note cover, lifestyle, lighting, or composition.",
            ),
            _node("product", WorkflowNodeType.PRODUCT_CONTEXT, title="Product info", x=48, y=200),
            _node(
                "angle",
                WorkflowNodeType.COPY_GENERATION,
                title="Cover angle",
                x=360,
                y=112,
                instruction_seed=_XHS_COPY,
            ),
            _node(
                "cover",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Vertical cover",
                x=700,
                y=80,
                instruction_seed=_XHS_IMAGE,
                size="1024x1536",
            ),
            _node(
                "cover_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Cover output",
                x=1040,
                y=80,
                config_json={"role": "output", "label": "Cover output"},
                output_slot_label="Cover output",
            ),
            _node(
                "detail",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Content companion image",
                x=700,
                y=232,
                instruction_seed="Reuse the cover angle and generate a lifestyle companion image that fits the body content while preserving an authentic usage atmosphere.",
                size="1024x1536",
            ),
            _node(
                "detail_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Companion image output",
                x=1040,
                y=232,
                config_json={"role": "output", "label": "Companion image output"},
                output_slot_label="Companion image output",
            ),
        ),
        edges=(
            ("style_reference", "angle"),
            ("product", "angle"),
            ("style_reference", "cover"),
            ("product", "cover"),
            ("angle", "cover"),
            ("cover", "cover_output"),
            ("cover_output", "detail"),
            ("detail", "detail_output"),
        ),
        output_slots=(
            _output_slot("cover_output", "Cover output", "Xiaohongshu note-cover candidates."),
            _output_slot("detail_output", "Companion image output", "Body-content companion image candidates."),
        ),
        reference_input_hints=(
            _reference_hint(
                "style_reference",
                role="style",
                label="Note style reference",
                description="Upload reference images for the target note cover, lifestyle, lighting, or composition.",
            ),
        ),
        suggested_connections=(
            _suggest("style_reference", "angle", "Style reference helps the cover copy match the target content feel."),
            _suggest("cover_output", "detail", "continue generating body companion images from the cover output to keep a consistent content tone."),
        ),
    ),
    _full_canvas_template(
        key="ecommerce-multi-angle-image-v1",
        title="Multi-angle images",
        description="Generate front, side, and back/key-structure compositions to round out the detail-page gallery.",
        scenario=_scenario(
            CanvasTemplateScenario.MULTI_ANGLE,
            title="Multi-angle",
            description="For detail-page carousel galleries; helps buyers see appearance, structure, and back-side details.",
            ecommerce_stage="gallery",
            tags=("angle", "gallery", "detail"),
        ),
        nodes=(
            _node("product", WorkflowNodeType.PRODUCT_CONTEXT, title="Product info", x=48, y=168),
            _node(
                "angle_plan",
                WorkflowNodeType.COPY_GENERATION,
                title="Angle planning",
                x=340,
                y=156,
                instruction_seed=_MULTI_ANGLE_COPY,
            ),
            _node(
                "front_image",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Front angle",
                x=680,
                y=48,
                instruction_seed=_MULTI_ANGLE_IMAGE,
                size="1024x1024",
            ),
            _node(
                "front_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Front view output",
                x=1020,
                y=48,
                config_json={"role": "output", "label": "Front view output"},
                output_slot_label="Front view output",
            ),
            _node(
                "side_image",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Side angle",
                x=680,
                y=180,
                instruction_seed="Generate a side or 45-degree shot of the same product with proportions and materials consistent with the front view.",
                size="1024x1024",
            ),
            _node(
                "side_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Side view output",
                x=1020,
                y=180,
                config_json={"role": "output", "label": "Side view output"},
                output_slot_label="Side view output",
            ),
            _node(
                "back_image",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Back / structure",
                x=680,
                y=312,
                instruction_seed="Generate a back, bottom, or key-structure angle of the same product, emphasising authentic structure and proportions.",
                size="1024x1024",
            ),
            _node(
                "back_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Structured output",
                x=1020,
                y=312,
                config_json={"role": "output", "label": "Structured output"},
                output_slot_label="Structured output",
            ),
        ),
        edges=(
            ("product", "angle_plan"),
            ("product", "front_image"),
            ("angle_plan", "front_image"),
            ("front_image", "front_output"),
            ("product", "side_image"),
            ("angle_plan", "side_image"),
            ("side_image", "side_output"),
            ("product", "back_image"),
            ("angle_plan", "back_image"),
            ("back_image", "back_output"),
        ),
        output_slots=(
            _output_slot("front_output", "Front view output", "Front-view candidates for the detail-page carousel."),
            _output_slot("side_output", "Side view output", "Side-view candidates for the detail-page carousel."),
            _output_slot("back_output", "Structured output", "Back, bottom, or key-structure candidates."),
        ),
        suggested_connections=(
            _suggest("angle_plan", "front_image", "Angle planning keeps multiple images aligned in order and subject proportions."),
            _suggest("angle_plan", "back_image", "The same plan constrains the back or structural composition so it does not drift from the product subject."),
        ),
    ),
    _full_canvas_template(
        key="ecommerce-sku-variant-image-v1",
        title="SKU / variant images",
        description="Generate differentiated display images for color, spec, or combination SKUs.",
        scenario=_scenario(
            CanvasTemplateScenario.SKU_VARIANT,
            title="SKU / variants",
            description="For explaining spec differences inside product detail.",
            ecommerce_stage="detail",
            tags=("sku", "variant", "detail"),
        ),
        nodes=(
            _node(
                "sku_reference",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="SKU reference image",
                x=48,
                y=48,
                config_json={"role": "product_reference", "label": "SKU reference image"},
                reference_input_hint="Upload reference images for the target SKU, color, or spec.",
            ),
            _node("product", WorkflowNodeType.PRODUCT_CONTEXT, title="Product info", x=48, y=204),
            _node(
                "variant_copy",
                WorkflowNodeType.COPY_GENERATION,
                title="Variant differences",
                x=360,
                y=120,
                instruction_seed=_SKU_COPY,
            ),
            _node(
                "single_variant",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Single-SKU image",
                x=700,
                y=64,
                instruction_seed=_SKU_IMAGE,
                size="1024x1024",
            ),
            _node(
                "single_variant_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="SKU image output",
                x=1040,
                y=64,
                config_json={"role": "output", "label": "SKU image output"},
                output_slot_label="SKU image output",
            ),
            _node(
                "variant_grid",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Variant comparison",
                x=700,
                y=224,
                instruction_seed="Generate a comparison image of color, spec, or combination SKUs from the same viewpoint with consistent lighting.",
                size="1536x1024",
            ),
            _node(
                "variant_grid_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Variant comparison output",
                x=1040,
                y=224,
                config_json={"role": "output", "label": "Variant comparison output"},
                output_slot_label="Variant comparison output",
            ),
        ),
        edges=(
            ("sku_reference", "variant_copy"),
            ("product", "variant_copy"),
            ("sku_reference", "single_variant"),
            ("product", "single_variant"),
            ("variant_copy", "single_variant"),
            ("single_variant", "single_variant_output"),
            ("sku_reference", "variant_grid"),
            ("product", "variant_grid"),
            ("variant_copy", "variant_grid"),
            ("variant_grid", "variant_grid_output"),
        ),
        output_slots=(
            _output_slot("single_variant_output", "SKU image output", "Candidates for the product spec selector area or detail-page imagery."),
            _output_slot("variant_grid_output", "Variant comparison output", "Multi-SKU comparison candidates."),
        ),
        reference_input_hints=(
            _reference_hint(
                "sku_reference",
                role="product_reference",
                label="SKU reference image",
                description="Upload reference images for the target SKU, color, or spec.",
            ),
        ),
        suggested_connections=(
            _suggest("sku_reference", "single_variant", "SKU reference images help keep color, spec, and subject consistent."),
            _suggest("variant_copy", "variant_grid", "Variant-difference notes are used to generate comparable multi-SKU images."),
        ),
    ),
    _full_canvas_template(
        key="ecommerce-feature-infographic-v1",
        title="Feature selling-points image",
        description="Turn the product's core features into a hierarchical infographic for detail-page above-the-fold persuasion.",
        scenario=_scenario(
            CanvasTemplateScenario.FEATURE_INFOGRAPHIC,
            title="Feature selling points",
            description="For detail-page selling-point explanations, feature entries, and conversion persuasion.",
            ecommerce_stage="detail",
            tags=("feature", "infographic", "detail"),
        ),
        nodes=(
            _node("product", WorkflowNodeType.PRODUCT_CONTEXT, title="Product info", x=48, y=176),
            _node(
                "feature_copy",
                WorkflowNodeType.COPY_GENERATION,
                title="Benefit extraction",
                x=340,
                y=92,
                instruction_seed=_FEATURE_INFOGRAPHIC_COPY,
            ),
            _node(
                "layout_copy",
                WorkflowNodeType.COPY_GENERATION,
                title="Information hierarchy",
                x=340,
                y=244,
                instruction_seed="Organise the selling points into an infographic hierarchy: main title, feature tags, icon/annotation positions, and whitespace areas.",
            ),
            _node(
                "infographic",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Benefit infographic",
                x=700,
                y=168,
                instruction_seed=_FEATURE_INFOGRAPHIC_IMAGE,
                size="1024x1536",
            ),
            _node(
                "infographic_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Benefit image output",
                x=1060,
                y=168,
                config_json={"role": "output", "label": "Benefit image output"},
                output_slot_label="Benefit image output",
            ),
        ),
        edges=(
            ("product", "feature_copy"),
            ("product", "layout_copy"),
            ("feature_copy", "layout_copy"),
            ("product", "infographic"),
            ("feature_copy", "infographic"),
            ("layout_copy", "infographic"),
            ("infographic", "infographic_output"),
        ),
        output_slots=(
            _output_slot("infographic_output", "Benefit image output", "Candidates for detail-page feature selling-points images."),
        ),
        suggested_connections=(
            _suggest("feature_copy", "layout_copy", "First extract the selling points, then structure the infographic hierarchy."),
            _suggest("layout_copy", "infographic", "Information hierarchy controls visual focus and whitespace."),
        ),
    ),
    _full_canvas_template(
        key="ecommerce-size-spec-image-v1",
        title="Size / spec image",
        description="Generate diagrams of size, capacity, material, and parameters to reduce pre-purchase questions.",
        scenario=_scenario(
            CanvasTemplateScenario.SIZE_SPEC,
            title="Size / spec",
            description="For detail-page parameter, size, capacity, and spec explanations.",
            ecommerce_stage="detail",
            tags=("size", "spec", "detail"),
        ),
        nodes=(
            _node("product", WorkflowNodeType.PRODUCT_CONTEXT, title="Product info", x=48, y=168),
            _node(
                "spec_copy",
                WorkflowNodeType.COPY_GENERATION,
                title="Specification summary",
                x=340,
                y=88,
                instruction_seed=_SIZE_SPEC_COPY,
            ),
            _node(
                "dimension_image",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Dimension-annotated image",
                x=680,
                y=64,
                instruction_seed=_SIZE_SPEC_IMAGE,
                size="1536x1024",
            ),
            _node(
                "dimension_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Size output",
                x=1020,
                y=64,
                config_json={"role": "output", "label": "Size output"},
                output_slot_label="Size output",
            ),
            _node(
                "spec_table_image",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Parameter diagram",
                x=680,
                y=236,
                instruction_seed="Generate a parameter diagram highlighting specs, materials, capacity, compatibility, and caution notes with a clean readable layout.",
                size="1024x1536",
            ),
            _node(
                "spec_table_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Parameter output",
                x=1020,
                y=236,
                config_json={"role": "output", "label": "Parameter output"},
                output_slot_label="Parameter output",
            ),
        ),
        edges=(
            ("product", "spec_copy"),
            ("product", "dimension_image"),
            ("spec_copy", "dimension_image"),
            ("dimension_image", "dimension_output"),
            ("product", "spec_table_image"),
            ("spec_copy", "spec_table_image"),
            ("spec_table_image", "spec_table_output"),
        ),
        output_slots=(
            _output_slot("dimension_output", "Size output", "Size-annotated detail-image candidates."),
            _output_slot("spec_table_output", "Parameter output", "Spec-and-parameter diagram candidates."),
        ),
        suggested_connections=(
            _suggest("spec_copy", "dimension_image", "Spec summaries provide accurate annotation points for the dimension-annotated image."),
            _suggest("spec_copy", "spec_table_image", "continue generating a parameter diagram from the same spec copy."),
        ),
    ),
    _full_canvas_template(
        key="ecommerce-scale-reference-image-v1",
        title="Scale reference image",
        description="Help buyers judge real size with handheld, worn, desktop, or spatial references.",
        scenario=_scenario(
            CanvasTemplateScenario.SCALE_REFERENCE,
            title="Scale reference",
            description="For explaining size, thickness, capacity, and on-body/on-table effects.",
            ecommerce_stage="detail",
            tags=("scale", "reference", "detail"),
        ),
        nodes=(
            _node(
                "scale_reference",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Object reference",
                x=48,
                y=52,
                config_json={"role": "scale", "label": "Object reference"},
                reference_input_hint="Upload reference images for the desired handheld, worn, desktop, or spatial reference.",
            ),
            _node("product", WorkflowNodeType.PRODUCT_CONTEXT, title="Product info", x=48, y=212),
            _node(
                "scale_copy",
                WorkflowNodeType.COPY_GENERATION,
                title="Scale notes",
                x=360,
                y=132,
                instruction_seed=_SCALE_REFERENCE_COPY,
            ),
            _node(
                "handheld_image",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Handheld / worn reference",
                x=710,
                y=72,
                instruction_seed=_SCALE_REFERENCE_IMAGE,
                size="1024x1536",
            ),
            _node(
                "handheld_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Scale image output",
                x=1060,
                y=72,
                config_json={"role": "output", "label": "Scale image output"},
                output_slot_label="Scale image output",
            ),
            _node(
                "surface_image",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Desktop / spatial reference",
                x=710,
                y=244,
                instruction_seed="Generate scale-reference images on desktops, walls, backpacks, kitchen counters, or storage spaces; avoid exaggerating product proportions.",
                size="1536x1024",
            ),
            _node(
                "surface_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Spatial reference output",
                x=1060,
                y=244,
                config_json={"role": "output", "label": "Spatial reference output"},
                output_slot_label="Spatial reference output",
            ),
        ),
        edges=(
            ("scale_reference", "scale_copy"),
            ("product", "scale_copy"),
            ("scale_reference", "handheld_image"),
            ("product", "handheld_image"),
            ("scale_copy", "handheld_image"),
            ("handheld_image", "handheld_output"),
            ("scale_reference", "surface_image"),
            ("product", "surface_image"),
            ("scale_copy", "surface_image"),
            ("surface_image", "surface_output"),
        ),
        output_slots=(
            _output_slot("handheld_output", "Scale image output", "Handheld, worn, or on-body scale-image candidates."),
            _output_slot("surface_output", "Spatial reference output", "Desktop, wall, or spatial-reference image candidates."),
        ),
        reference_input_hints=(
            _reference_hint(
                "scale_reference",
                role="scale",
                label="Object reference",
                description="Upload reference images for the desired handheld, worn, desktop, or spatial reference.",
            ),
        ),
        suggested_connections=(
            _suggest("scale_reference", "handheld_image", "Reference images constrain person, desktop, or spatial proportions."),
            _suggest("scale_copy", "surface_image", "Scale notes help spatial-reference images avoid proportional distortion."),
        ),
    ),
    _full_canvas_template(
        key="ecommerce-package-checklist-image-v1",
        title="Packaging / checklist image",
        description="Show the packaging, accessories, freebies, and unboxing contents to reduce pre-sales questions.",
        scenario=_scenario(
            CanvasTemplateScenario.PACKAGE_CHECKLIST,
            title="Packaging checklist",
            description="For detail-page unboxing content, accessory counts, and gift-box presentation.",
            ecommerce_stage="detail",
            tags=("package", "checklist", "detail"),
        ),
        nodes=(
            _node(
                "package_reference",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Packaging reference",
                x=48,
                y=56,
                config_json={"role": "package", "label": "Packaging reference"},
                reference_input_hint="Upload reference images for the packaging, accessories, freebies, gift boxes, or checklist layout.",
            ),
            _node("product", WorkflowNodeType.PRODUCT_CONTEXT, title="Product info", x=48, y=208),
            _node(
                "checklist_copy",
                WorkflowNodeType.COPY_GENERATION,
                title="ChecklistCopy",
                x=360,
                y=132,
                instruction_seed=_PACKAGE_COPY,
            ),
            _node(
                "flatlay_image",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Packaging flat-lay",
                x=700,
                y=88,
                instruction_seed=_PACKAGE_IMAGE,
                size="1536x1024",
            ),
            _node(
                "flatlay_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Checklist output",
                x=1040,
                y=88,
                config_json={"role": "output", "label": "Checklist output"},
                output_slot_label="Checklist output",
            ),
            _node(
                "gift_image",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Gift box / unboxing image",
                x=700,
                y=252,
                instruction_seed="Generate an image that emphasises unboxing, gift-box, or arrival state with the packaging and accessories clearly visible.",
                size="1024x1024",
            ),
            _node(
                "gift_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Unboxing output",
                x=1040,
                y=252,
                config_json={"role": "output", "label": "Unboxing output"},
                output_slot_label="Unboxing output",
            ),
        ),
        edges=(
            ("package_reference", "checklist_copy"),
            ("product", "checklist_copy"),
            ("package_reference", "flatlay_image"),
            ("product", "flatlay_image"),
            ("checklist_copy", "flatlay_image"),
            ("flatlay_image", "flatlay_output"),
            ("package_reference", "gift_image"),
            ("product", "gift_image"),
            ("checklist_copy", "gift_image"),
            ("gift_image", "gift_output"),
        ),
        output_slots=(
            _output_slot("flatlay_output", "Checklist output", "Packaging, accessory, and freebie flat-lay candidates."),
            _output_slot("gift_output", "Unboxing output", "Gift-box or unboxing image candidates."),
        ),
        reference_input_hints=(
            _reference_hint(
                "package_reference",
                role="package",
                label="Packaging reference",
                description="Upload reference images for the packaging, accessories, freebies, gift boxes, or checklist layout.",
            ),
        ),
        suggested_connections=(
            _suggest("package_reference", "flatlay_image", "Packaging references constrain flat-lay composition and accessory arrangement."),
            _suggest("checklist_copy", "gift_image", "Checklist copy ensures the unboxing image keeps the correct."),
        ),
    ),
    _full_canvas_template(
        key="ecommerce-usage-steps-image-v1",
        title="Usage-steps image",
        description="Generate installation, unboxing, wearing, or cleaning step diagrams to reduce the usage barrier.",
        scenario=_scenario(
            CanvasTemplateScenario.USAGE_STEPS,
            title="Usage steps",
            description="For installation guides, usage tutorials, cleaning and maintenance, and pre-sale explanations.",
            ecommerce_stage="detail",
            tags=("steps", "usage", "detail"),
        ),
        nodes=(
            _node(
                "step_reference",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Step reference",
                x=48,
                y=56,
                config_json={"role": "usage", "label": "Step reference"},
                reference_input_hint="Upload reference images for installation, wearing, unboxing, cleaning, or usage actions.",
            ),
            _node("product", WorkflowNodeType.PRODUCT_CONTEXT, title="Product info", x=48, y=220),
            _node(
                "step_copy",
                WorkflowNodeType.COPY_GENERATION,
                title="Step breakdown",
                x=360,
                y=132,
                instruction_seed=_USAGE_STEPS_COPY,
            ),
            _node(
                "step_image",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Step diagram",
                x=700,
                y=84,
                instruction_seed=_USAGE_STEPS_IMAGE,
                size="1024x1536",
            ),
            _node(
                "step_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Step output",
                x=1040,
                y=84,
                config_json={"role": "output", "label": "Step output"},
                output_slot_label="Step output",
            ),
            _node(
                "tip_copy",
                WorkflowNodeType.COPY_GENERATION,
                title="Caution notes",
                x=360,
                y=292,
                instruction_seed="Add caution notes, compatibility constraints, cleaning/maintenance reminders, or safety tips; keep them short and readable.",
            ),
            _node(
                "tip_image",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Caution notes image",
                x=700,
                y=292,
                instruction_seed="Generate a caution-notes diagram explaining limits, maintenance, or anti-patterns with icons and short tags.",
                size="1024x1024",
            ),
            _node(
                "tip_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Caution notes output",
                x=1040,
                y=292,
                config_json={"role": "output", "label": "Caution notes output"},
                output_slot_label="Caution notes output",
            ),
        ),
        edges=(
            ("step_reference", "step_copy"),
            ("product", "step_copy"),
            ("step_reference", "step_image"),
            ("product", "step_image"),
            ("step_copy", "step_image"),
            ("step_image", "step_output"),
            ("product", "tip_copy"),
            ("step_copy", "tip_copy"),
            ("step_reference", "tip_image"),
            ("product", "tip_image"),
            ("tip_copy", "tip_image"),
            ("tip_image", "tip_output"),
        ),
        output_slots=(
            _output_slot("step_output", "Step output", "Installation, unboxing, wearing, or usage-step image candidates."),
            _output_slot("tip_output", "Caution notes output", "Maintenance, compatibility, or anti-pattern diagram candidates."),
        ),
        reference_input_hints=(
            _reference_hint(
                "step_reference",
                role="usage",
                label="Step reference",
                description="Upload reference images for installation, wearing, unboxing, cleaning, or usage actions.",
            ),
        ),
        suggested_connections=(
            _suggest("step_copy", "tip_copy", "continue deriving caution notes from the step breakdown so key limits are not missed."),
            _suggest("tip_copy", "tip_image", "Caution-notes copy generates a standalone diagram for detail-page split presentation."),
        ),
    ),
    _full_canvas_template(
        key="ecommerce-comparison-image-v1",
        title="Comparison image",
        description="Generate comparison diagrams against old versions, standard models, competitors, or bundles.",
        scenario=_scenario(
            CanvasTemplateScenario.COMPARISON,
            title="Comparison",
            description="For explaining upgrade points, bundle differences, and purchase-decision dimensions.",
            ecommerce_stage="detail",
            tags=("comparison", "upgrade", "detail"),
        ),
        nodes=(
            _node(
                "compare_reference",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Comparison reference",
                x=48,
                y=56,
                config_json={"role": "comparison", "label": "Comparison reference"},
                reference_input_hint="Upload reference images for old versions, competitors, standard models, bundles, or comparison layouts.",
            ),
            _node("product", WorkflowNodeType.PRODUCT_CONTEXT, title="Product info", x=48, y=212),
            _node(
                "comparison_copy",
                WorkflowNodeType.COPY_GENERATION,
                title="Comparison dimensions",
                x=360,
                y=128,
                instruction_seed=_COMPARISON_COPY,
            ),
            _node(
                "comparison_image",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Comparison diagram",
                x=700,
                y=88,
                instruction_seed=_COMPARISON_IMAGE,
                size="1536x1024",
            ),
            _node(
                "comparison_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Comparison output",
                x=1040,
                y=88,
                config_json={"role": "output", "label": "Comparison output"},
                output_slot_label="Comparison output",
            ),
            _node(
                "upgrade_image",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Upgrade-points image",
                x=700,
                y=248,
                instruction_seed="Generate diagrams focused on upgrade points or bundle differences, emphasising verifiable material, structural, capacity, or feature differences.",
                size="1024x1024",
            ),
            _node(
                "upgrade_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Upgrade points output",
                x=1040,
                y=248,
                config_json={"role": "output", "label": "Upgrade points output"},
                output_slot_label="Upgrade points output",
            ),
        ),
        edges=(
            ("compare_reference", "comparison_copy"),
            ("product", "comparison_copy"),
            ("compare_reference", "comparison_image"),
            ("product", "comparison_image"),
            ("comparison_copy", "comparison_image"),
            ("comparison_image", "comparison_output"),
            ("compare_reference", "upgrade_image"),
            ("product", "upgrade_image"),
            ("comparison_copy", "upgrade_image"),
            ("upgrade_image", "upgrade_output"),
        ),
        output_slots=(
            _output_slot("comparison_output", "Comparison output", "Left-right or top-bottom comparison diagram candidates."),
            _output_slot("upgrade_output", "Upgrade points output", "Upgrade-points, bundle-difference, or old-vs-new difference image candidates."),
        ),
        reference_input_hints=(
            _reference_hint(
                "compare_reference",
                role="comparison",
                label="Comparison reference",
                description="Upload reference images for old versions, competitors, standard models, bundles, or comparison layouts.",
            ),
        ),
        suggested_connections=(
            _suggest("compare_reference", "comparison_image", "Comparison references help organise the image along target dimensions."),
            _suggest("comparison_copy", "upgrade_image", "continue generating a more focused upgrade-points image from the comparison dimensions."),
        ),
    ),
    _full_canvas_template(
        key="ecommerce-model-lifestyle-image-v1",
        title="Model / lifestyle image",
        description="Generate product scene images with people, outfits, or a lifestyle atmosphere.",
        scenario=_scenario(
            CanvasTemplateScenario.MODEL_LIFESTYLE,
            title="Model / lifestyle",
            description="For apparel, beauty, home, and other scenes that need a sense of real use.",
            ecommerce_stage="gallery",
            tags=("model", "lifestyle", "usage"),
        ),
        nodes=(
            _node(
                "style",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Pose/style reference",
                x=48,
                y=48,
                config_json={"role": "style", "label": "Pose/style reference"},
                reference_input_hint="Upload reference images for style, pose, scene, or model atmosphere.",
            ),
            _node("product", WorkflowNodeType.PRODUCT_CONTEXT, title="Product info", x=48, y=200),
            _node(
                "copy",
                WorkflowNodeType.COPY_GENERATION,
                title="Audience and scene",
                x=360,
                y=112,
                instruction_seed=_LIFESTYLE_COPY,
            ),
            _node(
                "half_body",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Half-body / in-use image",
                x=700,
                y=64,
                instruction_seed=_LIFESTYLE_IMAGE,
                size="1024x1536",
            ),
            _node(
                "half_body_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Lifestyle image",
                x=1040,
                y=64,
                config_json={"role": "output", "label": "Lifestyle image"},
                output_slot_label="Lifestyle image",
            ),
            _node(
                "detail_usage",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Usage-details image",
                x=700,
                y=224,
                instruction_seed="Generate images emphasising usage actions, tactile feel, oran image showing wearing details with the product clearly visible and a natural atmosphere. ",
                size="1024x1536",
            ),
            _node(
                "detail_usage_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Usage detail output",
                x=1040,
                y=224,
                config_json={"role": "output", "label": "Usage detail output"},
                output_slot_label="Usage detail output",
            ),
        ),
        edges=(
            ("style", "copy"),
            ("product", "copy"),
            ("style", "half_body"),
            ("product", "half_body"),
            ("copy", "half_body"),
            ("half_body", "half_body_output"),
            ("style", "detail_usage"),
            ("product", "detail_usage"),
            ("copy", "detail_usage"),
            ("detail_usage", "detail_usage_output"),
        ),
        output_slots=(
            _output_slot("half_body_output", "Lifestyle image", "Lifestyle image candidates for the person shot or detail-page gallery."),
            _output_slot("detail_usage_output", "Usage detail output", "Usage-action or partial-atmosphere image candidates."),
        ),
        reference_input_hints=(
            _reference_hint(
                "style",
                role="style",
                label="Pose/style reference",
                description="Upload reference images for style, pose, scene, or model atmosphere.",
            ),
        ),
        suggested_connections=(
            _suggest("style", "half_body", "Style references directly constrain pose, lighting, and atmosphere."),
            _suggest("copy", "detail_usage", "Generate usage-detail images from the same audience-and-scene notes."),
        ),
    ),
    _full_canvas_template(
        key="ecommerce-scene-image-v1",
        title="Scene image",
        description="Place the product into a relatable usage space or business scene.",
        scenario=_scenario(
            CanvasTemplateScenario.SCENE_IMAGE,
            title="scene",
            description="For explaining the product's usage environment, pairings, and spatial relationships.",
            ecommerce_stage="gallery",
            tags=("scene", "context", "usage"),
        ),
        nodes=(
            _node(
                "scene_reference",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Scene reference",
                x=48,
                y=64,
                config_json={"role": "scene", "label": "Scene reference"},
                reference_input_hint="Upload reference images for the target space, season, lighting, or display style.",
            ),
            _node("product", WorkflowNodeType.PRODUCT_CONTEXT, title="Product info", x=48, y=208),
            _node(
                "copy",
                WorkflowNodeType.COPY_GENERATION,
                title="Scene notes",
                x=360,
                y=136,
                instruction_seed=_SCENE_COPY,
            ),
            _node(
                "wide_scene",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Wide scene",
                x=700,
                y=136,
                instruction_seed=_SCENE_IMAGE,
                size="1536x1024",
            ),
            _node(
                "scene_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Scene image output",
                x=1040,
                y=136,
                config_json={"role": "output", "label": "Scene image output"},
                output_slot_label="Scene image output",
            ),
        ),
        edges=(
            ("scene_reference", "copy"),
            ("product", "copy"),
            ("scene_reference", "wide_scene"),
            ("product", "wide_scene"),
            ("copy", "wide_scene"),
            ("wide_scene", "scene_output"),
        ),
        output_slots=(
            _output_slot("scene_output", "Scene image output", "Usage-scenario image candidates for the detail-page gallery."),
        ),
        reference_input_hints=(
            _reference_hint(
                "scene_reference",
                role="scene",
                label="Scene reference",
                description="Upload reference images for the target space, season, lighting, or display style.",
            ),
        ),
        suggested_connections=(
            _suggest("scene_reference", "wide_scene", "Scene references directly constrain space, lighting, and display style."),
        ),
    ),
    _full_canvas_template(
        key="ecommerce-detail-material-image-v1",
        title="Detail / material image",
        description="Generate display images of material, craftsmanship, partial structure, or functional details.",
        scenario=_scenario(
            CanvasTemplateScenario.DETAIL_MATERIAL,
            title="Detail / material",
            description="For explaining material, craftsmanship, and key functions on the detail page.",
            ecommerce_stage="detail",
            tags=("detail", "material", "macro"),
        ),
        nodes=(
            _node(
                "detail_reference",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Detail reference image",
                x=48,
                y=56,
                config_json={"role": "detail", "label": "Detail reference image"},
                reference_input_hint="Upload detail reference images for material texture, partial structure, or craftsmanship.",
            ),
            _node("product", WorkflowNodeType.PRODUCT_CONTEXT, title="Product info", x=48, y=212),
            _node(
                "detail_copy",
                WorkflowNodeType.COPY_GENERATION,
                title="Detail notes",
                x=360,
                y=132,
                instruction_seed=_DETAIL_COPY,
            ),
            _node(
                "macro_image",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Material close-up",
                x=700,
                y=72,
                instruction_seed=_DETAIL_IMAGE,
                size="1024x1024",
            ),
            _node(
                "macro_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Detail image output",
                x=1040,
                y=72,
                config_json={"role": "output", "label": "Detail image output"},
                output_slot_label="Detail image output",
            ),
            _node(
                "structure_image",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Structure diagram",
                x=700,
                y=236,
                instruction_seed="Generate diagrams of partial structure, opening mechanism, interfaces, stitching, or craftsmanship; emphasise understandable structural relationships.",
                size="1024x1024",
            ),
            _node(
                "structure_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Structured output",
                x=1040,
                y=236,
                config_json={"role": "output", "label": "Structured output"},
                output_slot_label="Structured output",
            ),
        ),
        edges=(
            ("detail_reference", "detail_copy"),
            ("product", "detail_copy"),
            ("detail_reference", "macro_image"),
            ("product", "macro_image"),
            ("detail_copy", "macro_image"),
            ("macro_image", "macro_output"),
            ("detail_reference", "structure_image"),
            ("product", "structure_image"),
            ("detail_copy", "structure_image"),
            ("structure_image", "structure_output"),
        ),
        output_slots=(
            _output_slot("macro_output", "Detail image output", "Detail-page material or function diagram candidates."),
            _output_slot("structure_output", "Structured output", "Partial-structure, interface, or craftsmanship diagram candidates."),
        ),
        reference_input_hints=(
            _reference_hint(
                "detail_reference",
                role="detail",
                label="Detail reference image",
                description="Upload detail reference images for material texture, partial structure, or craftsmanship.",
            ),
        ),
        suggested_connections=(
            _suggest("detail_reference", "macro_image", "Detail references help close-ups keep textures and structures authentic."),
            _suggest("detail_copy", "structure_image", "continue generating clearer structural-relationship diagrams from the detail notes."),
        ),
    ),
    _full_canvas_template(
        key="ecommerce-campaign-promotion-image-v1",
        title="Campaign / promotion image",
        description="Generate product images suitable for campaign entries, promotion messaging, and promotional atmosphere.",
        scenario=_scenario(
            CanvasTemplateScenario.CAMPAIGN_PROMOTION,
            title="Campaign / promotion",
            description="For campaign pages, promotion slots, and on-platform creative assets.",
            ecommerce_stage="campaign",
            tags=("campaign", "promotion", "banner"),
        ),
        nodes=(
            _node(
                "campaign_style",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Campaign style reference",
                x=48,
                y=48,
                config_json={"role": "style", "label": "Campaign style reference"},
                reference_input_hint="Upload reference images for the campaign hero visual, brand color, festive atmosphere, or layout.",
            ),
            _node("product", WorkflowNodeType.PRODUCT_CONTEXT, title="Product info", x=48, y=216),
            _node(
                "offer_copy",
                WorkflowNodeType.COPY_GENERATION,
                title="Promotion info",
                x=360,
                y=80,
                instruction_seed=_CAMPAIGN_COPY,
            ),
            _node(
                "visual_copy",
                WorkflowNodeType.COPY_GENERATION,
                title="visualhierarchy",
                x=360,
                y=224,
                instruction_seed="Organise the campaign-image hierarchy: product subject, benefits, campaign atmosphere, and whitespace; avoid information overload.",
            ),
            _node(
                "banner",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Campaign landscape image",
                x=720,
                y=136,
                instruction_seed=_CAMPAIGN_IMAGE,
                size="1536x1024",
            ),
            _node(
                "banner_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Campaign image output",
                x=1080,
                y=136,
                config_json={"role": "output", "label": "Campaign image output"},
                output_slot_label="Campaign image output",
            ),
        ),
        edges=(
            ("campaign_style", "offer_copy"),
            ("product", "offer_copy"),
            ("campaign_style", "visual_copy"),
            ("product", "visual_copy"),
            ("campaign_style", "banner"),
            ("product", "banner"),
            ("offer_copy", "banner"),
            ("visual_copy", "banner"),
            ("banner", "banner_output"),
        ),
        output_slots=(
            _output_slot("banner_output", "Campaign image output", "Campaign-entry, promotion-slot, or ad-asset candidates."),
        ),
        reference_input_hints=(
            _reference_hint(
                "campaign_style",
                role="style",
                label="Campaign style reference",
                description="Upload reference images for the campaign hero visual, brand color, festive atmosphere, or layout.",
            ),
        ),
        suggested_connections=(
            _suggest("offer_copy", "banner", "Promotion info provides the campaign conversion focus."),
            _suggest("visual_copy", "banner", "Visual-hierarchy notes clarify whitespace and primary/secondary relationships."),
        ),
    ),
    _full_canvas_template(
        key="ecommerce-short-video-cover-v1",
        title="Short-video cover image",
        description="Generate vertical covers suitable for short-video entries, content feeds, and livestream warm-up videos.",
        scenario=_scenario(
            CanvasTemplateScenario.SHORT_VIDEO_COVER,
            title="Short-video cover",
            description="For in-app short videos, content feeds, livestream warm-ups, and ad entries.",
            ecommerce_stage="content",
            tags=("video", "cover", "content"),
        ),
        nodes=(
            _node(
                "cover_style",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Cover style reference",
                x=48,
                y=56,
                config_json={"role": "style", "label": "Cover style reference"},
                reference_input_hint="Upload reference images from short-video covers, livestream warm-ups, content feeds, or influencer-video screenshots.",
            ),
            _node("product", WorkflowNodeType.PRODUCT_CONTEXT, title="Product info", x=48, y=220),
            _node(
                "hook_copy",
                WorkflowNodeType.COPY_GENERATION,
                title="Cover hook",
                x=360,
                y=88,
                instruction_seed=_SHORT_VIDEO_COVER_COPY,
            ),
            _node(
                "frame_copy",
                WorkflowNodeType.COPY_GENERATION,
                title="Visual rhythm",
                x=360,
                y=244,
                instruction_seed="Organise the short-video cover rhythm: product close-up, usage moments, gaze, title area, and safe whitespace.",
            ),
            _node(
                "vertical_cover",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Vertical cover",
                x=720,
                y=96,
                instruction_seed=_SHORT_VIDEO_COVER_IMAGE,
                size="1024x1536",
            ),
            _node(
                "vertical_cover_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Short-video cover output",
                x=1080,
                y=96,
                config_json={"role": "output", "label": "Short-video cover output"},
                output_slot_label="Short-video cover output",
            ),
            _node(
                "closeup_cover",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Close-up cover",
                x=720,
                y=276,
                instruction_seed="Generate a backup short-video cover with stronger focus on product close-ups and usage moments; suitable for fast recognition in content feeds.",
                size="1024x1536",
            ),
            _node(
                "closeup_cover_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Close-up cover output",
                x=1080,
                y=276,
                config_json={"role": "output", "label": "Close-up cover output"},
                output_slot_label="Close-up cover output",
            ),
        ),
        edges=(
            ("cover_style", "hook_copy"),
            ("product", "hook_copy"),
            ("cover_style", "frame_copy"),
            ("product", "frame_copy"),
            ("cover_style", "vertical_cover"),
            ("product", "vertical_cover"),
            ("hook_copy", "vertical_cover"),
            ("frame_copy", "vertical_cover"),
            ("vertical_cover", "vertical_cover_output"),
            ("cover_style", "closeup_cover"),
            ("product", "closeup_cover"),
            ("hook_copy", "closeup_cover"),
            ("frame_copy", "closeup_cover"),
            ("closeup_cover", "closeup_cover_output"),
        ),
        output_slots=(
            _output_slot("vertical_cover_output", "Short-video cover output", "Short-video, content-feed, or livestream warm-up cover candidates."),
            _output_slot("closeup_cover_output", "Close-up cover output", "Cover candidates more focused on the product and usage moments."),
        ),
        reference_input_hints=(
            _reference_hint(
                "cover_style",
                role="style",
                label="Cover style reference",
                description="Upload reference images from short-video covers, livestream warm-ups, content feeds, or influencer-video screenshots.",
            ),
        ),
        suggested_connections=(
            _suggest("hook_copy", "vertical_cover", "The cover hook drives the first-glance message in the content feed."),
            _suggest("frame_copy", "closeup_cover", "Visual rhythm constrains the close-up cover's title area and product recognition."),
        ),
    ),
    _full_canvas_template(
        key="ecommerce-white-background-image-v1",
        title="White-background image",
        description="Generate white-background product images for platform standards, cutouts, or basic display.",
        scenario=_scenario(
            CanvasTemplateScenario.WHITE_BACKGROUND,
            title="white background",
            description="For platform baseline product images, spec diagrams, and asset reuse.",
            ecommerce_stage="listing",
            tags=("white-background", "clean", "marketplace"),
        ),
        nodes=(
            _node(
                "product_reference",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Subject reference image",
                x=48,
                y=56,
                config_json={"role": "product_reference", "label": "Subject reference image"},
                reference_input_hint="Upload reference images of the product subject whose appearance, angle, or proportions must be preserved.",
            ),
            _node("product", WorkflowNodeType.PRODUCT_CONTEXT, title="Product info", x=48, y=212),
            _node(
                "clean_copy",
                WorkflowNodeType.COPY_GENERATION,
                title="White-background requirements",
                x=360,
                y=132,
                instruction_seed=_WHITE_BACKGROUND_COPY,
            ),
            _node(
                "white_image",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Standard white-background image",
                x=700,
                y=80,
                instruction_seed=_WHITE_BACKGROUND_IMAGE,
                size="1024x1024",
            ),
            _node(
                "white_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="White-background image output",
                x=1040,
                y=80,
                config_json={"role": "output", "label": "White-background image output"},
                output_slot_label="White-background image output",
            ),
            _node(
                "shadow_image",
                WorkflowNodeType.IMAGE_GENERATION,
                title="Light-shadow display image",
                x=700,
                y=248,
                instruction_seed="Generate Light-shadow product display image on a light gray or pure white background; preserves material and volume. ",
                size="1024x1024",
            ),
            _node(
                "shadow_output",
                WorkflowNodeType.REFERENCE_IMAGE,
                title="Display image output",
                x=1040,
                y=248,
                config_json={"role": "output", "label": "Display image output"},
                output_slot_label="Display image output",
            ),
        ),
        edges=(
            ("product_reference", "clean_copy"),
            ("product", "clean_copy"),
            ("product_reference", "white_image"),
            ("product", "white_image"),
            ("clean_copy", "white_image"),
            ("white_image", "white_output"),
            ("product_reference", "shadow_image"),
            ("product", "shadow_image"),
            ("clean_copy", "shadow_image"),
            ("shadow_image", "shadow_output"),
        ),
        output_slots=(
            _output_slot("white_output", "White-background image output", "Platform-standard white-background image or base asset for follow-up edits. "),
            _output_slot("shadow_output", "Display image output", "Base-display image with a light shadow."),
        ),
        reference_input_hints=(
            _reference_hint(
                "product_reference",
                role="product_reference",
                label="Subject reference image",
                description="Upload reference images of the product subject whose appearance, angle, or proportions must be preserved.",
            ),
        ),
        suggested_connections=(
            _suggest("product_reference", "white_image", "Subject reference image used to keep the appearance, angle, and edge details."),
            _suggest("clean_copy", "shadow_image", "White-background requirements continue into a base-display version with a light shadow as requested."),
        ),
    ),
)
