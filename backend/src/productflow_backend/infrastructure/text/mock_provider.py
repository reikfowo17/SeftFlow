from __future__ import annotations

from productflow_backend.application.contracts import (
    BlocksCopyContent,
    CopyBlock,
    CopyNodeConfigV2,
    CopyPayloadV2,
    CreativeBriefPayload,
    ProductInput,
    ReferenceImageInput,
    VisualGuidance,
)
from productflow_backend.infrastructure.text.base import TextProvider


class MockTextProvider(TextProvider):
    provider_name = "mock"

    def generate_brief(self, product: ProductInput) -> tuple[CreativeBriefPayload, str]:
        category = product.category or "general ecommerce"
        note_hint = f", key reference: {product.source_note[:48]}" if product.source_note else ""
        brief = CreativeBriefPayload(
            positioning=f"{category}practical product for the scene{note_hint}",
            audience="value-conscious shoppers who want quick selling points",
            selling_angles=[
                "highlight the core purpose so buyers know which problem it solves",
                "emphasize tangible benefits, avoid vague adjectives",
                "language closer to marketplace main image and promotional poster style",
            ],
            taboo_phrases=["lowest price online", "cure-all claim", "guaranteed effective"],
            poster_style_hint="white background main image with bold red promotional callouts",
        )
        return brief, "mock-brief-v1"

    def generate_copy(
        self,
        product: ProductInput,
        brief: CreativeBriefPayload,
        config: CopyNodeConfigV2,
        reference_images: list[ReferenceImageInput] | None = None,
    ) -> tuple[CopyPayloadV2, str]:
        category_prefix = f"{product.category} " if product.category else ""
        price_line = f" Reference price {product.price}" if product.price else ""
        note_line = f", combine description: {product.source_note[:36]}" if product.source_note else ""
        instruction_line = f", current direction: {config.instruction[:32]}" if config.instruction else ""
        reference_images = reference_images or []
        reference_hint = ""
        if reference_images:
            first_reference = reference_images[0]
            label = first_reference.label or first_reference.filename
            role = first_reference.role or "Reference image"
            reference_hint = f", reference{role}: {label}"
        title = f"{category_prefix}{product.name} - practical and easy to use, ideal store hero"
        points = [
            f"clearer core purpose: {product.name}key points at a glance{note_line}{reference_hint}",
            "direct presentation, suitable for main image, detail pages, or promo assets",
            (
                f"language leans toward{config.tone or 'clear conversion'}, suits"
                f"{config.channel or 'ecommerce'}scene{price_line}{instruction_line}"
            ).strip(),
        ]
        copy = CopyPayloadV2(
            purpose=config.purpose,
            summary=title,
            content=BlocksCopyContent(
                blocks=[
                    CopyBlock(id="headline", role="headline", label="primary information", text=title, priority=1),
                    *[
                        CopyBlock(
                            id=f"point-{index}",
                            role="selling_point",
                            label=f"Selling points {index}",
                            text=point,
                            visual_hint="usable as on-canvas annotation or image side caption",
                            priority=index + 1,
                        )
                        for index, point in enumerate(points, start=1)
                    ],
                ]
            ),
            visual_guidance=VisualGuidance(
                main_message=title,
                hierarchy=["product subject", "core selling points", "supporting notes"],
                composition_hint=brief.poster_style_hint,
                text_density="medium",
                avoid=brief.taboo_phrases,
            ),
        )
        return copy, "mock-copy-v2"
