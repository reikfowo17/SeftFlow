from __future__ import annotations

from openai import OpenAI

from productflow_backend.application.contracts import (
    CopyNodeConfigV2,
    CopyPayloadV2,
    CreativeBriefPayload,
    ProductInput,
    ReferenceImageInput,
)
from productflow_backend.application.copy_payloads import normalize_copy_payload
from productflow_backend.config import get_runtime_settings
from productflow_backend.infrastructure.openai_response_parsing import read_json_object_from_response
from productflow_backend.infrastructure.prompts import text_or_default
from productflow_backend.infrastructure.provider_config import (
    ResolvedTextProviderConfig,
    resolve_text_provider_config,
)
from productflow_backend.infrastructure.text.base import TextProvider


class OpenAITextProvider(TextProvider):
    provider_name = "openai"
    prompt_version = "responses-json-v1"

    def __init__(self, provider_config: ResolvedTextProviderConfig | None = None) -> None:
        settings = get_runtime_settings()
        resolved_config = provider_config or resolve_text_provider_config()
        client_kwargs = {"api_key": resolved_config.api_key}
        if resolved_config.base_url:
            client_kwargs["base_url"] = resolved_config.base_url
        self.client = OpenAI(**client_kwargs)
        self.brief_model = resolved_config.brief_model
        self.copy_model = resolved_config.copy_model
        self.brief_system_prompt = settings.prompt_brief_system
        self.copy_system_prompt = settings.prompt_copy_system

    def _read_output_json(self, response) -> dict:
        return read_json_object_from_response(response, error_label="Copy provider")

    def generate_brief(self, product: ProductInput) -> tuple[CreativeBriefPayload, str]:
        response = self.client.responses.create(
            model=self.brief_model,
            instructions=text_or_default(self.brief_system_prompt, "Return concise structured JSON. "),
            input=[
                {
                    "role": "user",
                    "content": (
                        f"Product name: {product.name}\n"
                        f"Category: {product.category or 'not provided'}\n"
                        f"Price: {product.price or 'not provided'}\n"
                        f"Product description/notes: {product.source_note or 'not provided'}\n"
                        "Return fields: positioning, audience, selling_angles (3 to 5 items), "
                        "taboo_phrases, poster_style_hint. "
                    ),
                },
            ],
        )
        payload = CreativeBriefPayload.model_validate(self._read_output_json(response))
        return payload, self.brief_model

    def generate_copy(
        self,
        product: ProductInput,
        brief: CreativeBriefPayload,
        config: CopyNodeConfigV2 | None = None,
        reference_images: list[ReferenceImageInput] | None = None,
    ) -> tuple[CopyPayloadV2, str]:
        config = config or CopyNodeConfigV2()
        reference_images = reference_images or []
        reference_lines = [
            (
                f"{index}. {reference.label or reference.filename}"
                f" (role: {reference.role or 'Reference image'}, type: {reference.mime_type}, file: {reference.filename}) "
            )
            for index, reference in enumerate(reference_images, start=1)
        ]
        reference_text = "\n".join(reference_lines) if reference_lines else "notconnection"
        response = self.client.responses.create(
            model=self.copy_model,
            instructions=text_or_default(self.copy_system_prompt, "Return JSON only, do not emit markdown. "),
            input=[
                {
                    "role": "user",
                    "content": (
                        f"Product name: {product.name}\n"
                        f"Category: {product.category or 'not provided'}\n"
                        f"Price: {product.price or 'not provided'}\n"
                        f"Product description/notes: {product.source_note or 'not provided'}\n"
                        f"Reference image: {reference_text}\n"
                        f"Copy purpose: {config.purpose or 'not specified'}\n"
                        f"Output mode: {config.output_mode}\n"
                        f"Channel: {config.channel or 'not specified'}\n"
                        f"Tone: {config.tone or 'not specified'}\n"
                        f"Current copy requirement: {config.instruction or 'Compose copy freely from the product and scene'}\n"
                        f"Optional slots: {[slot.model_dump(mode='json') for slot in config.requested_slots]}\n"
                        f"Product positioning: {brief.positioning}\n"
                        f"Target audience: {brief.audience}\n"
                        f"Selling angles: {', '.join(brief.selling_angles)}\n"
                        f"Banned expressions: {', '.join(brief.taboo_phrases) or 'none'}\n"
                        "Return v2 JSON envelope: version=2, purpose, summary, content, visual_guidance. \n"
                        "content.kind must be freeform, blocks, or layout_brief. "
                        "Do not fabricate CTA, poster title, or a fixed list of 3 to 5 selling points just to fill fields. "
                    ),
                },
            ],
        )
        payload = normalize_copy_payload(self._read_output_json(response), fallback_purpose=config.purpose)
        return payload, self.copy_model
