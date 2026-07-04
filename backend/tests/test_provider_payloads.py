from __future__ import annotations

import logging
from base64 import b64encode
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from helpers import (
    _execute_workflow_queue_inline,
    _login,
    _make_demo_image_bytes,
    _make_demo_image_data_url,
    _wait_for_workflow_run,
)
from PIL import Image
from pydantic import ValidationError

from productflow_backend.application.contracts import (
    BlocksCopyContent,
    CopyBlock,
    CopyNodeConfigV2,
    CopyPayloadV2,
    CopySection,
    CreativeBriefPayload,
    FreeformCopyContent,
    LayoutBriefCopyContent,
    PosterGenerationInput,
    ProductInput,
    ReferenceImageInput,
)
from productflow_backend.application.copy_payloads import normalize_copy_payload
from productflow_backend.application.product_workflow_dependencies import WorkflowExecutionDependencies
from productflow_backend.application.product_workflows import run_product_workflow
from productflow_backend.application.use_cases import (
    create_product,
    get_product_detail,
)
from productflow_backend.config import get_settings
from productflow_backend.domain.enums import (
    PosterKind,
)
from productflow_backend.infrastructure.db.models import (
    AppSetting,
    ProviderBinding,
    ProviderProfile,
)
from productflow_backend.infrastructure.db.session import get_session_factory
from productflow_backend.infrastructure.image.gemini_provider import (
    GoogleGeminiImageClient,
    GoogleGeminiImageProvider,
    GoogleGeminiReferenceImage,
    map_productflow_size_to_gemini_image_config,
)
from productflow_backend.infrastructure.image.images_provider import OpenAIImagesImageProvider
from productflow_backend.infrastructure.image.responses_provider import OpenAIResponsesImageProvider
from productflow_backend.infrastructure.provider_config import ResolvedImageProviderConfig

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


def _progress_collector_with_context(
    *,
    task_id: str,
    session_id: str,
    candidate_index: int,
    candidate_count: int,
):
    events: list[dict] = []

    def append(progress: dict) -> None:
        events.append(progress)

    append.productflow_context = {  # type: ignore[attr-defined]
        "task_id": task_id,
        "session_id": session_id,
        "candidate_index": candidate_index,
        "candidate_count": candidate_count,
    }
    return append


class DummyImagesAPIItem:
    def __init__(self, b64_json: str | None, revised_prompt: str | None = "revised prompt") -> None:
        self.b64_json = b64_json
        self.revised_prompt = revised_prompt


class DummyImagesAPIResponse:
    def __init__(self, b64_json: str | None = None, *, b64_jsons: list[str | None] | None = None) -> None:
        self.data = [DummyImagesAPIItem(item) for item in (b64_jsons if b64_jsons is not None else [b64_json])]


def test_prompt_settings_reach_provider_prompt_builders(configured_env: Path, monkeypatch) -> None:
    from productflow_backend.infrastructure.image.chat_service import ImageChatService, ImageChatTurn
    from productflow_backend.infrastructure.prompts import render_prompt_template
    from productflow_backend.infrastructure.text.openai_provider import OpenAITextProvider

    assert render_prompt_template(
        "example JSON：{\"title\":\"{title}\"}；unknown：{unknown}；bad bracket：{",
        {"title": "main title"},
    ) == "example JSON：{\"title\":\"main title\"}；unknown：{unknown}；bad bracket：{"

    session = get_session_factory()()
    try:
        session.add_all(
            [
                AppSetting(key="prompt_brief_system", value="custom product understanding prompt"),
                AppSetting(key="prompt_copy_system", value="custom copy prompt"),
                AppSetting(
                    key="prompt_poster_image_template",
                    value=(
                        "custom poster {product_name} / {instruction} / {kind_label} / "
                        "{context_block} / {reference_policy}"
                    ),
                ),
                AppSetting(
                    key="prompt_poster_image_edit_template",
                    value="custom image edit {product_name} / {instruction} / {kind_label} / {size} / {reference_policy}",
                ),
                AppSetting(
                    key="prompt_poster_image_reference_policy",
                    value="custom visual reference rule",
                ),
                AppSetting(
                    key="prompt_image_chat_template",
                    value="custom continuous image generation {size} / {history_block} / {prompt}",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    text_calls: list[dict] = []

    class DummyTextResponse:
        def __init__(self, output_text: str) -> None:
            self.output_text = output_text

    class DummyTextResponses:
        def create(self, **kwargs):
            text_calls.append(kwargs)
            if len(text_calls) == 1:
                return DummyTextResponse(
                    '{"positioning":"entry positioning","audience":"novice","selling_angles":["steady","fast","save"],'
                    '"taboo_phrases":[],"poster_style_hint":"white background"}'
                )
            return DummyTextResponse(
                '{"version":2,"summary":"main title","content":{"kind":"blocks","blocks":[{"id":"headline","text":"title"}]}}'
            )

    class DummyTextOpenAI:
        def __init__(self, **kwargs) -> None:
            self.responses = DummyTextResponses()

    monkeypatch.setattr("productflow_backend.infrastructure.text.openai_provider.OpenAI", DummyTextOpenAI)

    text_provider = OpenAITextProvider()
    product_input = ProductInput(
        name="test product",
        category="category",
        price="9.90",
        source_note="note",
        image_path="/tmp/a.png",
    )
    brief, _ = text_provider.generate_brief(product_input)
    text_provider.generate_copy(product_input, brief)

    assert text_calls[0]["instructions"] == "custom product understanding prompt"
    assert text_calls[0]["input"][0]["role"] == "user"
    assert text_calls[1]["instructions"] == "custom copy prompt"
    assert text_calls[1]["input"][0]["role"] == "user"

    source_path = configured_env / "prompt-provider-source.png"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(_make_demo_image_bytes())
    poster_prompt = OpenAIResponsesImageProvider()._build_prompt(
        PosterGenerationInput(
            product_name="test product",
            category="category",
            price="9.90",
            source_note="note",
            instruction="emphasize lightness",
            structured_copy_context="summary：main title\nselling point：selling point one\nselling point：selling point two\nselling point：selling point three",
            source_image=source_path,
        ),
        PosterKind.MAIN_IMAGE,
        "1024x1024",
    )
    assert "custom poster test product / emphasize lightness / Main image /" in poster_prompt
    assert "Available copy references" in poster_prompt
    assert "selling point：selling point one" in poster_prompt
    assert poster_prompt.endswith("custom visual reference rule")

    edit_prompt = OpenAIResponsesImageProvider()._build_prompt(
        PosterGenerationInput(
            copy_prompt_mode="image_edit",
            product_name="test product",
            category="category",
            price="9.90",
            source_note="note",
            instruction="switch to white background，keep subject",
            source_image=source_path,
        ),
        PosterKind.MAIN_IMAGE,
        "1024x1024",
    )
    assert edit_prompt == "custom image edit test product / switch to white background，keep subject / Main image / 1024x1024 / custom visual reference rule"

    chat_prompt = ImageChatService()._build_prompt(
        "switch to white background",
        [ImageChatTurn(role="user", content="do one first main image")],
        "1024x1024",
    )
    assert "custom continuous image generation 1024x1024" in chat_prompt
    assert "User: do one first main image" in chat_prompt
    assert chat_prompt.endswith("switch to white background")


def test_openai_text_provider_reads_sse_text_response() -> None:
    from productflow_backend.infrastructure.text.openai_provider import OpenAITextProvider

    provider = object.__new__(OpenAITextProvider)
    response = "\n".join(
        [
            'event: response.output_text.delta',
            'data: {"type":"response.output_text.delta","delta":"{\\"version\\":2,"}',
            "",
            'event: response.output_text.delta',
            'data: {"type":"response.output_text.delta","delta":"\\"summary\\":\\"promotioncopy\\","}',
            "",
            'event: response.output_text.delta',
            (
                'data: {"type":"response.output_text.delta","delta":"\\"content\\":'
                '{\\"kind\\":\\"freeform\\",\\"text\\":\\"May Day sale\\"}}"}'
            ),
            "",
            "event: response.completed",
            'data: {"type":"response.completed","response":{"output":[]}}',
        ]
    )

    assert provider._read_output_json(response) == {
        "version": 2,
        "summary": "promotioncopy",
        "content": {"kind": "freeform", "text": "May Day sale"},
    }


def test_ai_payload_normalizes_scalar_text_lists_without_swallowing_malformed_values() -> None:
    brief = CreativeBriefPayload.model_validate(
        {
            "positioning": ["photography starter kit", "desktop shooting aid"],
            "audience": ["photography beginner", "Xiaohongshu image-text creator"],
            "selling_angles": ["quick to learn", "compositionsteady", "natural photo"],
            "taboo_phrases": [],
            "poster_style_hint": ["clean and bright", "real life feel"],
        }
    )
    assert brief.positioning == "photography starter kit、desktop shooting aid"
    assert brief.audience == "photography beginner、Xiaohongshu image-text creator"
    assert brief.poster_style_hint == "clean and bright、real life feel"

    for bad_value in ([], ["photography beginner", ""], [{"label": "photography beginner"}]):
        with pytest.raises(ValidationError):
            CreativeBriefPayload.model_validate(
                {
                    "positioning": "photography starter kit",
                    "audience": bad_value,
                    "selling_angles": ["quick to learn", "compositionsteady", "natural photo"],
                    "taboo_phrases": [],
                    "poster_style_hint": "clean and bright",
                }
            )


def test_copy_payload_v2_supports_flexible_content() -> None:
    freeform = CopyPayloadV2(summary="white background image keeps subject only", content=FreeformCopyContent(text="subject centered, keep real materials."))
    blocks = CopyPayloadV2(
        summary="selling pointquick view",
        content=BlocksCopyContent(
            blocks=[
                CopyBlock(id="a", label="drill-free", text="no wall damage，easier install", visual_hint="wall annotation"),
                CopyBlock(id="b", label="load", text="kitchen jarsteadyfixed storage", visual_hint="load icon"),
            ]
        ),
    )
    layout = CopyPayloadV2(
        summary="info image hierarchy",
        content=LayoutBriefCopyContent(
            sections=[
                CopySection(
                    id="hero",
                    title="main title area",
                    body="drill-freestorage",
                    items=[CopyBlock(id="point", text="place below 2  function label")],
                    visual_hint="top whitespace fortitle",
                )
            ]
        ),
    )

    assert freeform.content.text == "subject centered, keep real materials."
    assert blocks.content.blocks[1].text == "kitchen jarsteadyfixed storage"
    assert layout.content.sections[0].title == "main title area"


def test_copy_payload_v2_normalizes_provider_block_variants() -> None:
    payload = normalize_copy_payload(
        {
            "version": 2,
            "summary": "coordinate acceptanceproduct4",
            "content": {
                "kind": "blocks",
                "blocks": [
                    {"type": "title", "text": "coordinate acceptanceproduct4"},
                    {"type": "benefit", "text": "covers listing workflow acceptance"},
                    {"type": "benefit", "text": "node、regional functionality test"},
                    {"type": "benefit", "text": "for quick identification and management"},
                    {
                        "type": "benefits",
                        "items": ["auto save", "sync before run", "presentation and datavalidate"],
                    },
                ],
            },
        }
    )

    assert payload.content.kind == "blocks"
    assert [block.id for block in payload.content.blocks] == [
        "title-1",
        "benefit-2",
        "benefit-3",
        "benefit-4",
        "benefits-5",
    ]
    assert payload.content.blocks[0].role == "title"
    assert payload.content.blocks[1].text == "covers listing workflow acceptance"
    assert payload.content.blocks[4].text == "auto save; sync before run; presentation and datavalidate"
    assert [block.text for block in payload.content.blocks[1:3]] == ["covers listing workflow acceptance", "node、regional functionality test"]


def test_copy_payload_v2_normalizes_real_provider_freeform_variants() -> None:
    items_payload = normalize_copy_payload(
        {
            "version": 2,
            "summary": "Xiaohongshu cover angle",
            "content": {
                "kind": "freeform",
                "items": ["fewer holes for rentals", "fits kitchen and bathroom", "double layer and hooks chosen by space"],
            },
        }
    )
    list_text_payload = normalize_copy_payload(
        {
            "version": 2,
            "summary": "audience and scene",
            "content": {
                "kind": "freeform",
                "text": ["tidier rented kitchen counters", "tiered storage of bathroom bottles"],
            },
        }
    )
    dict_text_payload = normalize_copy_payload(
        {
            "version": 2,
            "summary": "scenenote",
            "content": {
                "kind": "freeform",
                "text": {"suitable scene": ["kitchen spice bottle", "bathroom wash bottle"], "note": "no guarantee for all wall types"},
            },
        }
    )
    chinese_key_payload = normalize_copy_payload(
        {
            "version": 2,
            "summary": "detailnote",
            "content": {
                "kind": "freeform",
                "short label": "304 stainless steel",
                "note": "bottom drain holes reduce standing water，screws suggested for heavy items。",
            },
        }
    )

    assert items_payload.content.text == "fewer holes for rentals\nfits kitchen and bathroom\ndouble layer and hooks chosen by space"
    assert list_text_payload.content.text == "tidier rented kitchen counters\ntiered storage of bathroom bottles"
    assert "suitable scene: kitchen spice bottle\nbathroom wash bottle" in dict_text_payload.content.text
    assert "short label: 304 stainless steel" in chinese_key_payload.content.text
    assert "note: bottom drain holes reduce standing water" in chinese_key_payload.content.text


def test_copy_payload_v2_normalizes_real_provider_layout_variants() -> None:
    payload = normalize_copy_payload(
        {
            "version": 2,
            "summary": "multi-angle plan",
            "content": {
                "kind": "layout_brief",
                "items": [
                    {
                        "order": 1,
                        "angle": "front main visual",
                        "copy": "show the rack body and front edge，titleemphasizedrill-freekitchen and bath storage。",
                        "shot": "subject centered，keep wall and counter references。",
                    },
                    {
                        "label": "bottom drain",
                        "description": "close up of bottom drain holes，notewash bottles and cleaning tools placed neatly。",
                        "visual_suggestion": "use partial-zoom annotations。",
                    },
                ],
            },
        }
    )

    assert payload.content.kind == "layout_brief"
    assert len(payload.content.sections) == 2
    assert payload.content.sections[0].id == "frontmainvisual-1"
    assert payload.content.sections[0].title == "front main visual"
    assert payload.content.sections[0].body == "show the rack body and front edge，titleemphasizedrill-freekitchen and bath storage。"
    assert payload.content.sections[0].visual_hint == "subject centered，keep wall and counter references。"
    assert payload.content.sections[1].title == "bottom drain"
    assert payload.content.sections[1].body == "close up of bottom drain holes，notewash bottles and cleaning tools placed neatly。"


def test_copy_payload_v2_normalizes_visual_guidance_text() -> None:
    payload = normalize_copy_payload(
        {
            "version": 2,
            "summary": "cool gray phone case",
            "content": {
                "kind": "blocks",
                "blocks": [{"id": "headline", "text": "suits commuting and daily looks"}],
            },
            "visual_guidance": "suits cool gray pairing、black and white background，highlight clean artistic visuals。",
        }
    )

    assert payload.visual_guidance is not None
    assert payload.visual_guidance.composition_hint == "suits cool gray pairing、black and white background，highlight clean artistic visuals。"


def test_copy_payload_v2_normalizes_layout_object_fields_to_sections() -> None:
    payload = normalize_copy_payload(
        {
            "version": 2,
            "summary": "visual hierarchy",
            "content": {
                "kind": "layout_brief",
                "hero_area": {"title": "main title area", "copy": "kitchen and bath storage，limited time deal69from"},
                "feature_points": [
                    {"label": "304stainless steel", "text": "easy to maintain in damp kitchens and bathrooms"},
                    {"label": "drill-free installation", "text": "can be reinforced with screws if needed"},
                ],
                "disclaimer": ["discounts subject to page", "load capacity and wall related items"],
            },
        }
    )

    assert payload.content.kind == "layout_brief"
    assert [section.title for section in payload.content.sections] == [
        "main title area",
        "feature points",
        "disclaimer",
    ]
    assert payload.content.sections[0].body == "kitchen and bath storage，limited time deal69from"
    assert [item.label for item in payload.content.sections[1].items] == [
        "304stainless steel",
        "drill-free installation",
    ]
    assert [item.text for item in payload.content.sections[1].items] == [
        "easy to maintain in damp kitchens and bathrooms",
        "can be reinforced with screws if needed",
    ]
    assert payload.content.sections[2].body == "discounts subject to page\nload capacity and wall related items"


def test_copy_payload_v2_drops_empty_provider_blocks() -> None:
    payload = normalize_copy_payload(
        {
            "version": 2,
            "summary": "comparison dimension",
            "content": {
                "kind": "blocks",
                "blocks": [
                    {"type": "compare_label", "text": "drill-free installation"},
                    {"type": "separator", "text": ""},
                    {"type": "compare_label", "text": "304 stainless steel"},
                ],
            },
        }
    )

    assert payload.content.kind == "blocks"
    assert [block.text for block in payload.content.blocks] == ["drill-free installation", "304 stainless steel"]


def test_copy_payload_v2_drops_empty_layout_items() -> None:
    payload = normalize_copy_payload(
        {
            "version": 2,
            "summary": "image rhythm",
            "content": {
                "kind": "layout_brief",
                "sections": [
                    {
                        "title": "3seconds",
                        "items": [
                            {"label": "empty shot", "text": ""},
                            {"label": "hook", "text": "counter messy？wall mounted tidy"},
                        ],
                    }
                ],
            },
        }
    )

    assert payload.content.kind == "layout_brief"
    assert [item.text for item in payload.content.sections[0].items] == ["counter messy？wall mounted tidy"]


def test_product_workflow_copy_run_normalizes_provider_scalar_lists(configured_env: Path, monkeypatch) -> None:
    class ListAudienceTextProvider:
        provider_name = "list-audience"
        prompt_version = "test-v1"

        def generate_brief(self, product: ProductInput) -> tuple[CreativeBriefPayload, str]:
            return (
                CreativeBriefPayload.model_validate(
                    {
                        "positioning": f"{product.name} content creation scene",
                        "audience": ["photography beginner", "Xiaohongshu image-text creator"],
                        "selling_angles": ["quick to learn", "imagesteady", "suits image content"],
                        "taboo_phrases": [],
                        "poster_style_hint": "fresh and real",
                    }
                ),
                "list-audience-brief",
            )

        def generate_copy(
            self,
            product: ProductInput,
            brief: CreativeBriefPayload,
            config: CopyNodeConfigV2,
            reference_images: list[ReferenceImageInput] | None = None,
        ) -> tuple[CopyPayloadV2, str]:
            del config, reference_images
            return (
                CopyPayloadV2(
                    summary=f"{product.name} novice shoots bettersteady",
                    content=BlocksCopyContent(
                        blocks=[
                            CopyBlock(id="audience", text=f"suits{brief.audience}"),
                            CopyBlock(id="stability", text="phone shooting angle bettersteadyfixed"),
                            CopyBlock(id="daily", text="Daily image-text content shoots better"),
                        ]
                    ),
                ),
                "list-audience-copy",
            )

    _execute_workflow_queue_inline(
        monkeypatch,
        dependencies=WorkflowExecutionDependencies(
            text_provider_resolver=lambda: ListAudienceTextProvider(),
        ),
    )

    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post(
        "/api/products",
        data={"name": "phone photography mount"},
        files={"image": ("tripod.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]

    run_response = client.post(f"/api/products/{product_id}/workflow/run", json={})
    assert run_response.status_code == 200
    assert run_response.json()["runs"][0]["status"] == "running"
    workflow_payload = _wait_for_workflow_run(client, product_id, status="succeeded")
    copy_node = next(node for node in workflow_payload["nodes"] if node["node_type"] == "copy_generation")
    assert copy_node["output_json"]["structured_payload"]["version"] == 2
    assert not set(REMOVED_COPY_OUTPUT_KEYS) & set(copy_node["output_json"])
    structured_text = str(copy_node["output_json"]["structured_payload"])
    assert "photography beginner、Xiaohongshu image-text creator" in structured_text

    product_response = client.get(f"/api/products/{product_id}")
    assert product_response.status_code == 200
    latest_brief = product_response.json()["latest_brief"]
    assert latest_brief["payload"]["audience"] == "photography beginner、Xiaohongshu image-text creator"


def test_product_workflow_copy_run_retries_provider_payload_contract_mismatch(
    configured_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RetryingTextProvider:
        provider_name = "retrying-text"
        prompt_version = "test-v1"
        brief_attempts = 0
        copy_attempts = 0

        def generate_brief(self, product: ProductInput) -> tuple[CreativeBriefPayload, str]:
            type(self).brief_attempts += 1
            if type(self).brief_attempts == 1:
                return CreativeBriefPayload.model_validate(
                    {
                        "positioning": f"{product.name} positioning",
                        "audience": [],
                        "selling_angles": ["light", "steady", "good storage"],
                        "taboo_phrases": [],
                        "poster_style_hint": "fresh",
                    }
                ), "bad-brief"
            return (
                CreativeBriefPayload(
                    positioning=f"{product.name} portable positioning",
                    audience="small home users",
                    selling_angles=["light", "steady", "good storage"],
                    taboo_phrases=[],
                    poster_style_hint="fresh and real",
                ),
                "good-brief",
            )

        def generate_copy(
            self,
            product: ProductInput,
            brief: CreativeBriefPayload,
            config: CopyNodeConfigV2,
            reference_images: list[ReferenceImageInput] | None = None,
        ) -> tuple[CopyPayloadV2, str]:
            del product, brief, config, reference_images
            type(self).copy_attempts += 1
            if type(self).copy_attempts == 1:
                return CopyPayloadV2.model_validate(
                    {
                        "version": 2,
                        "summary": " ",
                        "content": {"kind": "freeform", "text": "first field mismatch"},
                    }
                ), "bad-copy"
            return (
                CopyPayloadV2(
                    summary="light and stable，easy storage",
                    content=FreeformCopyContent(text="light and stable，suits small homes daily storage。"),
                ),
                "good-copy",
            )

    _execute_workflow_queue_inline(
        monkeypatch,
        dependencies=WorkflowExecutionDependencies(
            text_provider_resolver=lambda: RetryingTextProvider(),
        ),
    )

    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post(
        "/api/products",
        data={"name": "foldable rack"},
        files={"image": ("shelf.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]

    run_response = client.post(f"/api/products/{product_id}/workflow/run", json={})
    assert run_response.status_code == 200
    workflow_payload = _wait_for_workflow_run(client, product_id, status="succeeded")
    copy_node = next(node for node in workflow_payload["nodes"] if node["node_type"] == "copy_generation")

    assert RetryingTextProvider.brief_attempts == 2
    assert RetryingTextProvider.copy_attempts == 2
    assert copy_node["output_json"]["summary"] == "Copy: light and stable，easy storage"
    assert copy_node["output_json"]["structured_payload"]["summary"] == "light and stable，easy storage"


def test_mock_image_provider_does_not_read_runtime_settings_during_generation(
    configured_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.infrastructure.image.mock_provider import MockImageProvider

    source_path = configured_env / "mock-thread-safe-source.png"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(_make_demo_image_bytes())
    provider = MockImageProvider()

    def fail_runtime_settings_lookup():
        raise AssertionError("runtime settings should be resolved before provider worker execution")

    monkeypatch.setattr(
        "productflow_backend.infrastructure.image.mock_provider.get_runtime_settings",
        fail_runtime_settings_lookup,
    )

    generated, model = provider.generate_poster_image(
        PosterGenerationInput(
            product_name="thread safetest product",
            category="test category",
            price="99",
            source_note="testnote",
            instruction="generated test image",
            structured_copy_context="summary：testmain title\nselling point：selling point one\nselling point：selling point two\nselling point：selling point three",
            source_image=source_path,
            image_size="512x512",
        ),
        PosterKind.MAIN_IMAGE,
    )

    assert model == "mock-image-v1"
    assert generated.width == 512
    assert generated.height == 512
    assert generated.bytes_data

def test_image_generation_without_copy_link_uses_image_edit_prompt_mode(
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
                    variant_label=f"capturing-{poster.copy_prompt_mode}",
                    provider_response_id="resp_workflow_1",
                    provider_response_status="completed",
                    provider_output_json={
                        "_productflow": {
                            "actual_size": "800x800",
                            "notes": ["accepted quality", "normalized size"],
                        },
                        "raw": {"hidden": True},
                    },
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
        data={"name": "camping cup"},
        files={"image": ("cup.png", _make_demo_image_bytes(), "image/png")},
    )
    assert created.status_code == 201
    product_id = created.json()["id"]

    workflow_response = client.get(f"/api/products/{product_id}/workflow")
    assert workflow_response.status_code == 200
    workflow = workflow_response.json()
    copy_node = next(node for node in workflow["nodes"] if node["node_type"] == "copy_generation")
    image_node = next(node for node in workflow["nodes"] if node["node_type"] == "image_generation")

    deleted_copy = client.delete(f"/api/workflow-nodes/{copy_node['id']}")
    assert deleted_copy.status_code == 200
    patched_image = client.patch(
        f"/api/workflow-nodes/{image_node['id']}",
        json={"config_json": {"instruction": "based onproduct imagechange to warm camping scene", "size": "1024x1024"}},
    )
    assert patched_image.status_code == 200

    selected_run = client.post(
        f"/api/products/{product_id}/workflow/run",
        json={"start_node_id": image_node["id"]},
    )
    assert selected_run.status_code == 200
    payload = _wait_for_workflow_run(client, product_id, status="succeeded")
    image_output = next(node for node in payload["nodes"] if node["id"] == image_node["id"])["output_json"]

    assert image_output["context_summary"]["copy_prompt_mode"] == "image_edit"
    assert image_output["copy_set_id"]
    assert image_output["provider_results"] == [
        {
            "target_index": 1,
            "provider_name": "capturing",
            "model_name": "capturing-v1",
            "provider_response_id": "resp_workflow_1",
            "provider_response_status": "completed",
            "actual_size": "800x800",
            "notes": ["accepted quality", "normalized size"],
        }
    ]
    assert len(captured_inputs) == 1
    assert captured_inputs[0].copy_prompt_mode == "image_edit"
    assert captured_inputs[0].instruction and "warm camping scene" in captured_inputs[0].instruction

def test_image_session_openai_responses_uses_explicit_branch_context(
    configured_env: Path,
    monkeypatch,
) -> None:
    from productflow_backend.presentation.api import create_app

    monkeypatch.setenv("IMAGE_PROVIDER_KIND", "openai_responses")
    monkeypatch.setenv("IMAGE_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("IMAGE_API_KEY", "demo-api-key")
    monkeypatch.setenv("IMAGE_GENERATE_MODEL", "gpt-5.4")
    get_settings.cache_clear()

    calls: list[dict] = []
    client_kwargs: list[dict] = []
    encoded_result = _make_demo_image_data_url().split(",", maxsplit=1)[1]

    class DummyImageGenerationCall:
        type = "image_generation_call"

        def __init__(self, index: int) -> None:
            self.id = f"ig_{index}"
            self.result = encoded_result
            self.revised_prompt = f"revised prompt {index}"

        def model_dump(self, *, mode: str, exclude_none: bool) -> dict[str, str]:
            return {
                "id": self.id,
                "type": self.type,
                "status": "completed",
                "revised_prompt": self.revised_prompt,
                "result": self.result,
            }

    class DummyResponse:
        def __init__(self, index: int) -> None:
            self.id = f"resp_{index}"
            self.output = [DummyImageGenerationCall(index)]

        def model_dump(self, *, mode: str, exclude_none: bool) -> dict:
            return {
                "id": self.id,
                "output": [output.model_dump(mode=mode, exclude_none=exclude_none) for output in self.output],
            }

    class DummyResponses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return DummyResponse(len(calls))

    class DummyOpenAI:
        def __init__(self, **kwargs) -> None:
            client_kwargs.append(kwargs)
            self.responses = DummyResponses()

    monkeypatch.setattr("productflow_backend.infrastructure.image.responses_provider.OpenAI", DummyOpenAI)

    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post("/api/image-sessions", json={"title": "Responses continuousimage generation"})
    assert created.status_code == 201
    session_id = created.json()["id"]

    upload = client.post(
        f"/api/image-sessions/{session_id}/reference-images",
        files={"reference_images": ("sample.png", _make_demo_image_bytes(), "image/png")},
    )
    assert upload.status_code == 200
    reference_id = next(asset["id"] for asset in upload.json()["assets"] if asset["kind"] == "reference_upload")

    first = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "generate anime styleproductscene", "size": "1024x1024"},
    )
    assert first.status_code == 202
    first_round = first.json()["rounds"][-1]
    assert first_round["provider_name"] == "openai-responses"
    assert first_round["provider_response_id"] == "resp_1"
    assert first_round["previous_response_id"] is None
    assert first_round["image_generation_call_id"] == "ig_1"
    first_asset_id = first_round["generated_asset"]["id"]

    second_without_base = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "keep subject，change background to a sunny street corner", "size": "1024x1024"},
    )
    assert second_without_base.status_code == 400
    assert second_without_base.json()["detail"] == "Follow-up image generation must select a previously generated image from this session as the base image"

    branched = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={
            "prompt": "only continue from first image and manually selected reference image",
            "size": "1024x1024",
            "base_asset_id": first_asset_id,
            "selected_reference_asset_ids": [reference_id],
        },
    )
    assert branched.status_code == 202
    branched_round = branched.json()["rounds"][-1]
    assert branched_round["provider_response_id"] == "resp_2"
    assert branched_round["previous_response_id"] is None
    assert branched_round["base_asset_id"] == first_asset_id
    assert branched_round["selected_reference_asset_ids"] == [reference_id]

    assert client_kwargs[0] == {"api_key": "demo-api-key", "base_url": "https://example.test/v1"}
    assert calls[0]["model"] == "gpt-5.4"
    assert calls[0]["tools"] == [{"type": "image_generation", "size": "1024x1024"}]
    assert "previous_response_id" not in calls[0]
    assert "previous_response_id" not in calls[1]
    assert isinstance(calls[0]["input"], str)
    branch_content = calls[1]["input"][0]["content"]
    assert branch_content[0]["type"] == "input_text"
    branch_images = [item for item in branch_content if item["type"] == "input_image"]
    assert len(branch_images) == 2
    assert all(item["image_url"].startswith("data:image/png;base64,") for item in branch_images)
    assert "/images/generations" not in str(calls)
    assert "/images/edits" not in str(calls)

def test_openai_responses_poster_provider_uses_image_generation_tool(
    configured_env: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("IMAGE_PROVIDER_KIND", "openai_responses")
    monkeypatch.setenv("IMAGE_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("IMAGE_API_KEY", "demo-api-key")
    monkeypatch.setenv("IMAGE_GENERATE_MODEL", "gpt-5.4")
    monkeypatch.setenv("IMAGE_RESPONSES_BACKGROUND_ENABLED", "false")
    get_settings.cache_clear()

    calls: list[dict] = []
    encoded_result = _make_demo_image_data_url().split(",", maxsplit=1)[1]

    class DummyImageGenerationCall:
        type = "image_generation_call"
        id = "ig_poster"
        result = encoded_result

        def model_dump(self, *, mode: str, exclude_none: bool) -> dict[str, str]:
            return {"id": self.id, "type": self.type, "result": self.result}

    class DummyResponse:
        id = "resp_poster"
        output = [DummyImageGenerationCall()]

        def model_dump(self, *, mode: str, exclude_none: bool) -> dict:
            return {"id": self.id, "output": [self.output[0].model_dump(mode=mode, exclude_none=exclude_none)]}

    class DummyResponses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return DummyResponse()

    class DummyOpenAI:
        def __init__(self, **kwargs) -> None:
            self.responses = DummyResponses()

    monkeypatch.setattr("productflow_backend.infrastructure.image.responses_provider.OpenAI", DummyOpenAI)

    source_path = configured_env / "provider-source.png"
    reference_path = configured_env / "provider-reference.png"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(_make_demo_image_bytes())
    reference_path.write_bytes(_make_demo_image_bytes())

    provider = OpenAIResponsesImageProvider()
    generated_image, model_name = provider.generate_poster_image(
        poster=PosterGenerationInput(
            product_name="test product",
            category="test category",
            price="9.90",
            source_note="waterproof Oxford fabric，suits commuting and short trips。",
            instruction="cleaner background，emphasize storage space。",
            image_size="1536x1024",
            tool_options={"quality": "high", "output_format": "webp"},
            structured_copy_context="summary：testposter title\nselling point：selling point1\nselling point：selling point2\nselling point：selling point3",
            source_image=source_path,
            reference_images=[
                ReferenceImageInput(
                    path=reference_path,
                    mime_type="image/png",
                    filename="reference.png",
                )
            ],
        ),
        kind=PosterKind.MAIN_IMAGE,
    )

    assert generated_image.mime_type == "image/png"
    assert (generated_image.width, generated_image.height) == (800, 800)
    assert model_name == "gpt-5.4"
    payload = calls[0]
    assert payload["model"] == "gpt-5.4"
    assert payload["tools"] == [
        {"type": "image_generation", "size": "1536x1024", "quality": "high", "output_format": "webp"}
    ]
    content = payload["input"][0]["content"]
    assert content[0]["type"] == "input_text"
    prompt_text = content[0]["text"]
    assert "cleaner background，emphasize storage space" in prompt_text
    assert "- Notes: waterproof Oxford fabric，suits commuting and short trips。" in prompt_text
    assert "- Number of reference images: 2" in prompt_text
    assert "- Product source image: the first input image" in prompt_text
    assert "- Reference image: reference.png (role: Reference image)" in prompt_text
    assert "Visual reference rules:" in prompt_text
    assert "If input images are provided, use the product/subject in the input images as the visual baseline" in prompt_text
    assert len([item for item in content if item["type"] == "input_image"]) == 2
    assert "/images/generations" not in str(payload)
    assert "/images/edits" not in str(payload)
    assert "background" not in payload


def test_openai_responses_image_tool_optional_fields_are_omitted_until_configured(
    configured_env: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("IMAGE_PROVIDER_KIND", "openai_responses")
    monkeypatch.setenv("IMAGE_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("IMAGE_API_KEY", "demo-api-key")
    monkeypatch.setenv("IMAGE_GENERATE_MODEL", "gpt-5.4")
    monkeypatch.setenv("IMAGE_RESPONSES_BACKGROUND_ENABLED", "false")
    get_settings.cache_clear()

    calls: list[dict] = []
    encoded_result = _make_demo_image_data_url().split(",", maxsplit=1)[1]

    class DummyImageGenerationCall:
        type = "image_generation_call"
        id = "ig_tool"
        result = encoded_result

        def model_dump(self, *, mode: str, exclude_none: bool) -> dict[str, str]:
            return {"id": self.id, "type": self.type, "result": self.result}

    class DummyResponse:
        id = "resp_tool"
        output = [DummyImageGenerationCall()]

        def model_dump(self, *, mode: str, exclude_none: bool) -> dict:
            return {"id": self.id, "output": [self.output[0].model_dump(mode=mode, exclude_none=exclude_none)]}

    class DummyResponses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return DummyResponse()

    class DummyOpenAI:
        def __init__(self, **kwargs) -> None:
            self.responses = DummyResponses()

    monkeypatch.setattr("productflow_backend.infrastructure.image.responses_provider.OpenAI", DummyOpenAI)

    from productflow_backend.infrastructure.image.responses_provider import OpenAIResponsesImageClient

    OpenAIResponsesImageClient().generate_image(prompt="default payload", size="1024x1024")

    assert calls[-1]["model"] == "gpt-5.4"
    assert calls[-1]["tools"] == [{"type": "image_generation", "size": "1024x1024"}]
    assert "background" not in calls[-1]
    assert "tool_choice" not in calls[-1]

    session = get_session_factory()()
    try:
        session.add_all(
            [
                AppSetting(
                    key="image_tool_allowed_fields",
                    value=(
                        "model,quality,output_format,output_compression,background,moderation,action,"
                        "input_fidelity,partial_images,n"
                    ),
                ),
                AppSetting(key="image_tool_model", value="gpt-image-2"),
                AppSetting(key="image_tool_quality", value="high"),
                AppSetting(key="image_tool_output_format", value="jpeg"),
                AppSetting(key="image_tool_output_compression", value="80"),
                AppSetting(key="image_tool_background", value="transparent"),
                AppSetting(key="image_tool_moderation", value="low"),
                AppSetting(key="image_tool_action", value="generate"),
                AppSetting(key="image_tool_input_fidelity", value="high"),
                AppSetting(key="image_tool_partial_images", value="2"),
                AppSetting(key="image_tool_n", value="3"),
            ]
        )
        session.commit()
    finally:
        session.close()

    OpenAIResponsesImageClient().generate_image(prompt="with optional field", size="1024x1536")

    assert calls[-1]["tools"] == [
        {
            "type": "image_generation",
            "size": "1024x1536",
            "model": "gpt-image-2",
            "quality": "high",
            "output_format": "jpeg",
            "output_compression": 80,
            "background": "transparent",
            "moderation": "low",
            "action": "generate",
            "input_fidelity": "high",
            "partial_images": 2,
        }
    ]
    assert "tool_choice" not in calls[-1]


def test_openai_responses_image_client_polls_background_response_and_reports_progress(
    configured_env: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("IMAGE_PROVIDER_KIND", "openai_responses")
    monkeypatch.setenv("IMAGE_API_KEY", "demo-api-key")
    monkeypatch.setenv("IMAGE_GENERATE_MODEL", "gpt-5.4")
    monkeypatch.setenv("IMAGE_RESPONSES_BACKGROUND_ENABLED", "true")
    get_settings.cache_clear()

    encoded_result = _make_demo_image_data_url().split(",", maxsplit=1)[1]
    calls: list[dict] = []
    retrieved: list[str] = []
    progress_events: list[dict] = []

    class DummyImageGenerationCall:
        type = "image_generation_call"
        id = "ig_background"
        result = encoded_result

        def model_dump(self, *, mode: str, exclude_none: bool) -> dict[str, str]:
            return {"id": self.id, "type": self.type, "result": self.result}

    class DummyResponse:
        def __init__(self, status: str, output: list | None = None) -> None:
            self.id = "resp_background"
            self.status = status
            self.output = output or []

        def model_dump(self, *, mode: str, exclude_none: bool) -> dict:
            return {
                "id": self.id,
                "status": self.status,
                "output": [
                    output.model_dump(mode=mode, exclude_none=exclude_none)
                    for output in self.output
                ],
            }

    class DummyResponses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return DummyResponse("queued")

        def retrieve(self, response_id: str):
            retrieved.append(response_id)
            return DummyResponse("completed", [DummyImageGenerationCall()])

    class DummyOpenAI:
        def __init__(self, **kwargs) -> None:
            self.responses = DummyResponses()

    monkeypatch.setattr("productflow_backend.infrastructure.image.responses_provider.OpenAI", DummyOpenAI)
    monkeypatch.setattr("productflow_backend.infrastructure.image.responses_provider.sleep", lambda seconds: None)

    from productflow_backend.infrastructure.image.responses_provider import OpenAIResponsesImageClient

    result = OpenAIResponsesImageClient().generate_image(
        prompt="background generation",
        size="1024x1024",
        progress_callback=progress_events.append,
    )

    assert calls[0]["background"] is True
    assert retrieved == ["resp_background"]
    assert result.provider_response_id == "resp_background"
    assert result.provider_output_json["status"] == "completed"
    assert result.image_generation_call_id == "ig_background"
    assert [event["provider_response_status"] for event in progress_events] == ["queued", "completed"]
    assert [event["provider_response_id"] for event in progress_events] == ["resp_background", "resp_background"]
    assert progress_events[-1]["provider_response"]["output"][0]["result"].startswith("<base64 omitted")


def test_openai_responses_image_client_falls_back_when_background_is_unsupported(
    configured_env: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("IMAGE_PROVIDER_KIND", "openai_responses")
    monkeypatch.setenv("IMAGE_API_KEY", "demo-api-key")
    monkeypatch.setenv("IMAGE_GENERATE_MODEL", "gpt-5.4")
    monkeypatch.setenv("IMAGE_RESPONSES_BACKGROUND_ENABLED", "true")
    get_settings.cache_clear()

    encoded_result = _make_demo_image_data_url().split(",", maxsplit=1)[1]
    calls: list[dict] = []

    class DummyImageGenerationCall:
        type = "image_generation_call"
        id = "ig_sync_fallback"
        result = encoded_result

        def model_dump(self, *, mode: str, exclude_none: bool) -> dict[str, str]:
            return {"id": self.id, "type": self.type, "result": self.result}

    class DummyResponse:
        id = "resp_sync_fallback"
        output = [DummyImageGenerationCall()]

        def model_dump(self, *, mode: str, exclude_none: bool) -> dict:
            return {"id": self.id, "output": [self.output[0].model_dump(mode=mode, exclude_none=exclude_none)]}

    class DummyResponses:
        def create(self, **kwargs):
            calls.append(kwargs)
            if kwargs.get("background") is True:
                raise RuntimeError("unexpected keyword argument: background")
            return DummyResponse()

    class DummyOpenAI:
        def __init__(self, **kwargs) -> None:
            self.responses = DummyResponses()

    monkeypatch.setattr("productflow_backend.infrastructure.image.responses_provider.OpenAI", DummyOpenAI)

    from productflow_backend.infrastructure.image.responses_provider import OpenAIResponsesImageClient

    result = OpenAIResponsesImageClient().generate_image(prompt="compatible sync response", size="1024x1024")

    assert len(calls) == 2
    assert calls[0]["background"] is True
    assert "background" not in calls[1]
    assert result.provider_response_id == "resp_sync_fallback"
    assert result.image_generation_call_id == "ig_sync_fallback"


def test_openai_responses_image_client_wraps_background_poll_errors(
    configured_env: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("IMAGE_PROVIDER_KIND", "openai_responses")
    monkeypatch.setenv("IMAGE_API_KEY", "demo-api-key")
    monkeypatch.setenv("IMAGE_GENERATE_MODEL", "gpt-5.4")
    monkeypatch.setenv("IMAGE_RESPONSES_BACKGROUND_ENABLED", "true")
    get_settings.cache_clear()

    class DummyResponse:
        id = "resp_poll_error"
        status = "queued"
        output = []

        def model_dump(self, *, mode: str, exclude_none: bool) -> dict:
            return {"id": self.id, "status": self.status, "output": []}

    class DummyResponses:
        def create(self, **kwargs):
            return DummyResponse()

        def retrieve(self, response_id: str):
            raise RuntimeError("raw provider failure with secret material")

    class DummyOpenAI:
        def __init__(self, **kwargs) -> None:
            self.responses = DummyResponses()

    monkeypatch.setattr("productflow_backend.infrastructure.image.responses_provider.OpenAI", DummyOpenAI)
    monkeypatch.setattr("productflow_backend.infrastructure.image.responses_provider.sleep", lambda seconds: None)

    from productflow_backend.infrastructure.image import responses_provider
    from productflow_backend.infrastructure.image.responses_provider import OpenAIResponsesImageClient

    log_messages: list[str] = []

    class DummyLogger:
        def log(self, level: int, message: str, *args) -> None:
            log_messages.append(message % args)

        def warning(self, message: str, *args) -> None:
            log_messages.append(message % args)

    monkeypatch.setattr(responses_provider, "logger", DummyLogger())

    with pytest.raises(RuntimeError) as exc_info:
        OpenAIResponsesImageClient().generate_image(
            prompt="pollfailed",
            size="1024x1024",
            progress_callback=_progress_collector_with_context(
                task_id="task-1",
                session_id="session-1",
                candidate_index=2,
                candidate_count=4,
            ),
        )

    assert str(exc_info.value) == "Image provider request failed; check provider configuration and retry"
    assert "secret material" not in str(exc_info.value)
    log_text = "\n".join(log_messages)
    assert "task_id=task-1" in log_text
    assert "session_id=session-1" in log_text
    assert "candidate_index=2" in log_text
    assert "candidate_count=4" in log_text
    assert "model=gpt-5.4" in log_text
    assert "background=True" in log_text
    assert "status=queued" in log_text
    assert "response_id=resp_poll_error" in log_text
    assert "exception_class=RuntimeError" in log_text
    assert "secret material" not in log_text
    assert "demo-api-key" not in log_text


def test_openai_responses_image_client_redacts_base_url_credentials_in_logs(
    configured_env: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("IMAGE_PROVIDER_KIND", "openai_responses")
    monkeypatch.setenv("IMAGE_API_KEY", "demo-api-key")
    monkeypatch.setenv("IMAGE_BASE_URL", "https://user:secret-pass@example.test/v1?token=secret-token")
    monkeypatch.setenv("IMAGE_GENERATE_MODEL", "gpt-5.4")
    get_settings.cache_clear()

    class DummyResponses:
        def create(self, **kwargs):
            raise RuntimeError("provider failure")

    class DummyOpenAI:
        def __init__(self, **kwargs) -> None:
            self.responses = DummyResponses()

    monkeypatch.setattr("productflow_backend.infrastructure.image.responses_provider.OpenAI", DummyOpenAI)

    from productflow_backend.infrastructure.image import responses_provider
    from productflow_backend.infrastructure.image.responses_provider import OpenAIResponsesImageClient

    log_messages: list[str] = []

    class DummyLogger:
        def log(self, level: int, message: str, *args) -> None:
            if level >= logging.WARNING:
                log_messages.append(message % args)

    monkeypatch.setattr(responses_provider, "logger", DummyLogger())

    with pytest.raises(RuntimeError):
        OpenAIResponsesImageClient().generate_image(prompt="log redaction", size="1024x1024")

    log_text = "\n".join(log_messages)
    assert "base_url=https://example.test/v1" in log_text
    assert "secret-pass" not in log_text
    assert "secret-token" not in log_text
    assert "demo-api-key" not in log_text


def test_openai_responses_image_client_retries_without_optional_fields_and_records_note(
    configured_env: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("IMAGE_PROVIDER_KIND", "openai_responses")
    monkeypatch.setenv("IMAGE_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("IMAGE_API_KEY", "demo-api-key")
    monkeypatch.setenv("IMAGE_GENERATE_MODEL", "gpt-5.4")
    get_settings.cache_clear()

    calls: list[dict] = []
    encoded_result = _make_demo_image_data_url().split(",", maxsplit=1)[1]

    class DummyImageGenerationCall:
        type = "image_generation_call"
        id = "ig_fallback"
        result = encoded_result
        size = "1024x1024"

        def model_dump(self, *, mode: str, exclude_none: bool) -> dict[str, str]:
            return {"id": self.id, "type": self.type, "result": self.result, "size": self.size}

    class DummyResponse:
        id = "resp_fallback"
        output = [DummyImageGenerationCall()]

        def model_dump(self, *, mode: str, exclude_none: bool) -> dict:
            return {"id": self.id, "output": [self.output[0].model_dump(mode=mode, exclude_none=exclude_none)]}

    class DummyResponses:
        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError("raw provider 400 unsupported field image_tool_quality")
            return DummyResponse()

    class DummyOpenAI:
        def __init__(self, **kwargs) -> None:
            self.responses = DummyResponses()

    monkeypatch.setattr("productflow_backend.infrastructure.image.responses_provider.OpenAI", DummyOpenAI)

    from productflow_backend.infrastructure.image.responses_provider import OpenAIResponsesImageClient

    result = OpenAIResponsesImageClient().generate_image(
        prompt="with per-round field",
        size="1024x1024",
        tool_options={"quality": "high", "output_format": "webp", "n": 2},
    )

    assert len(calls) == 2
    assert calls[0]["tools"] == [
        {"type": "image_generation", "size": "1024x1024", "quality": "high", "output_format": "webp"}
    ]
    assert calls[1]["tools"] == [{"type": "image_generation", "size": "1024x1024"}]
    assert result.provider_request_json["_productflow"]["fallback_used"] is True
    assert result.provider_output_json["_productflow"]["notes"] == [
            {"kind": "fallback", "message": "Provider did not support some parameters, falling back to defaults. "}
    ]
    assert "unsupported field" not in str(result.provider_output_json)


def test_openai_responses_image_client_records_provider_adjusted_note(
    configured_env: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("IMAGE_PROVIDER_KIND", "openai_responses")
    monkeypatch.setenv("IMAGE_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("IMAGE_API_KEY", "demo-api-key")
    monkeypatch.setenv("IMAGE_GENERATE_MODEL", "gpt-5.4")
    get_settings.cache_clear()

    encoded_result = _make_demo_image_data_url().split(",", maxsplit=1)[1]

    class DummyImageGenerationCall:
        type = "image_generation_call"
        id = "ig_adjusted"
        result = encoded_result
        output_format = "png"
        quality = "auto"

        def model_dump(self, *, mode: str, exclude_none: bool) -> dict[str, str]:
            return {
                "id": self.id,
                "type": self.type,
                "result": self.result,
                "output_format": self.output_format,
                "quality": self.quality,
            }

    class DummyResponse:
        id = "resp_adjusted"
        output = [DummyImageGenerationCall()]

        def model_dump(self, *, mode: str, exclude_none: bool) -> dict:
            return {"id": self.id, "output": [self.output[0].model_dump(mode=mode, exclude_none=exclude_none)]}

    class DummyResponses:
        def create(self, **kwargs):
            return DummyResponse()

    class DummyOpenAI:
        def __init__(self, **kwargs) -> None:
            self.responses = DummyResponses()

    monkeypatch.setattr("productflow_backend.infrastructure.image.responses_provider.OpenAI", DummyOpenAI)

    from productflow_backend.infrastructure.image.responses_provider import OpenAIResponsesImageClient

    result = OpenAIResponsesImageClient().generate_image(
        prompt="provider adjust field",
        size="1024x1024",
        tool_options={"quality": "high", "output_format": "webp"},
    )

    metadata = result.provider_output_json["_productflow"]
    assert metadata["effective_image_tool"]["output_format"] == "png"
    assert metadata["notes"][0]["kind"] == "provider_adjusted"
    assert metadata["notes"][0]["fields"] == ["quality", "output_format"]


def test_openai_responses_image_client_sanitizes_client_initialization_errors(
    configured_env: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("IMAGE_PROVIDER_KIND", "openai_responses")
    monkeypatch.setenv("IMAGE_BASE_URL", "https://secret-provider.example/v1")
    monkeypatch.setenv("IMAGE_API_KEY", "sk-sensitive")
    monkeypatch.setenv("IMAGE_GENERATE_MODEL", "gpt-5.4")
    get_settings.cache_clear()

    class DummyOpenAI:
        def __init__(self, **kwargs) -> None:
            raise RuntimeError(f"raw provider init failed: {kwargs}")

    monkeypatch.setattr("productflow_backend.infrastructure.image.responses_provider.OpenAI", DummyOpenAI)

    from productflow_backend.infrastructure.image.responses_provider import OpenAIResponsesImageClient

    with pytest.raises(RuntimeError) as error:
        OpenAIResponsesImageClient().generate_image(prompt="initfailed", size="1024x1024")

    assert str(error.value) == "Image provider request failed; check provider configuration and retry"
    assert isinstance(error.value.__cause__, RuntimeError)
    assert "sk-sensitive" not in str(error.value)
    assert "secret-provider" not in str(error.value)


def test_openai_responses_image_client_infers_mime_type_from_returned_bytes(
    configured_env: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("IMAGE_PROVIDER_KIND", "openai_responses")
    monkeypatch.setenv("IMAGE_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("IMAGE_API_KEY", "demo-api-key")
    monkeypatch.setenv("IMAGE_GENERATE_MODEL", "gpt-5.4")
    get_settings.cache_clear()

    buffer = BytesIO()
    Image.new("RGB", (16, 16), (255, 255, 255)).save(buffer, format="JPEG")
    encoded_result = b64encode(buffer.getvalue()).decode("utf-8")

    class DummyImageGenerationCall:
        type = "image_generation_call"
        id = "ig_jpeg"
        result = encoded_result
        output_format = "png"

        def model_dump(self, *, mode: str, exclude_none: bool) -> dict[str, str]:
            return {
                "id": self.id,
                "type": self.type,
                "result": self.result,
                "output_format": self.output_format,
            }

    class DummyResponse:
        id = "resp_jpeg"
        output = [DummyImageGenerationCall()]

        def model_dump(self, *, mode: str, exclude_none: bool) -> dict:
            return {"id": self.id, "output": [self.output[0].model_dump(mode=mode, exclude_none=exclude_none)]}

    class DummyResponses:
        def create(self, **kwargs):
            return DummyResponse()

    class DummyOpenAI:
        def __init__(self, **kwargs) -> None:
            self.responses = DummyResponses()

    monkeypatch.setattr("productflow_backend.infrastructure.image.responses_provider.OpenAI", DummyOpenAI)

    from productflow_backend.infrastructure.image.responses_provider import OpenAIResponsesImageClient

    result = OpenAIResponsesImageClient().generate_image(prompt="return JPEG", size="1024x1024")

    assert result.mime_type == "image/jpeg"


def test_openai_images_provider_factory_and_client_generate_payload(
    configured_env: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("IMAGE_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("IMAGE_API_KEY", "demo-api-key")
    monkeypatch.setenv("IMAGE_PROVIDER_KIND", "openai_images")
    monkeypatch.setenv("IMAGE_GENERATE_MODEL", "gpt-image-1")
    monkeypatch.setenv("IMAGE_IMAGES_QUALITY", "high")
    monkeypatch.setenv("IMAGE_IMAGES_STYLE", "vivid")
    get_settings.cache_clear()

    calls: list[dict] = []
    client_kwargs: list[dict] = []
    encoded_result = _make_demo_image_data_url().split(",", maxsplit=1)[1]

    class DummyImages:
        def generate(self, **kwargs):
            calls.append(kwargs)
            return DummyImagesAPIResponse(encoded_result)

    class DummyOpenAI:
        def __init__(self, **kwargs) -> None:
            client_kwargs.append(kwargs)
            self.images = DummyImages()

    monkeypatch.setattr("productflow_backend.infrastructure.image.images_provider.OpenAI", DummyOpenAI)

    from productflow_backend.infrastructure.image.factory import get_image_provider
    from productflow_backend.infrastructure.image.images_provider import OpenAIImagesClient

    assert isinstance(get_image_provider(), OpenAIImagesImageProvider)

    result = OpenAIImagesClient().generate(prompt="generate product image", size="1024x1024")[0]

    assert client_kwargs == [{"api_key": "demo-api-key", "base_url": "https://example.test/v1"}]
    assert calls == [
        {
            "model": "gpt-image-1",
            "prompt": "generate product image",
            "size": "1024x1024",
            "n": 1,
            "response_format": "b64_json",
            "quality": "high",
            "style": "vivid",
        }
    ]
    assert result.mime_type == "image/png"
    assert result.model_name == "gpt-image-1"
    assert result.provider_request_json == {
        "model": "gpt-image-1",
        "prompt": "generate product image",
        "size": "1024x1024",
        "n": 1,
        "quality": "high",
        "style": "vivid",
    }
    assert result.provider_output_json == {}


def test_google_gemini_provider_factory_and_client_generate_payload(
    configured_env: Path,
    monkeypatch,
) -> None:
    session = get_session_factory()()
    try:
        profile = ProviderProfile(
            name="Gemini",
            provider_type="google_gemini",
            base_url=None,
            api_key="google-api-key",
            capabilities_json=["image_google_gemini"],
            default_models_json={"image_model": "gemini-2.5-flash-image"},
            config_json={},
            enabled=True,
        )
        session.add(profile)
        session.flush()
        session.add(
            ProviderBinding(
                purpose="image",
                provider_kind="google_gemini_image",
                provider_profile_id=profile.id,
                model_settings_json={"model": "gemini-3.1-flash-image-preview"},
                config_json={"gemini_api_version": "v1beta", "gemini_output_mime_type": "image/png"},
            )
        )
        session.commit()
    finally:
        session.close()

    calls: list[dict] = []
    client_kwargs: list[dict] = []
    image_bytes = _make_demo_image_bytes()

    class DummyModels:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                response_id="gemini-response-1",
                model_version="gemini-test-version",
                parts=[
                    SimpleNamespace(text="ok"),
                    SimpleNamespace(inline_data=SimpleNamespace(data=image_bytes, mime_type="image/png")),
                ],
            )

    class DummyClient:
        def __init__(self, **kwargs) -> None:
            client_kwargs.append(kwargs)
            self.models = DummyModels()

    monkeypatch.setattr("productflow_backend.infrastructure.image.gemini_provider.genai.Client", DummyClient)

    from productflow_backend.infrastructure.image.factory import get_image_provider

    assert isinstance(get_image_provider(), GoogleGeminiImageProvider)

    result = GoogleGeminiImageClient().generate_image(
        prompt="generate product image",
        size="2048x1152",
        reference_images=[GoogleGeminiReferenceImage(image_bytes, "image/png", "ref.png")],
    )

    assert client_kwargs[0]["api_key"] == "google-api-key"
    assert calls[0]["model"] == "gemini-3.1-flash-image-preview"
    assert len(calls[0]["contents"]) == 2
    assert result.mime_type == "image/png"
    assert result.model_name == "gemini-3.1-flash-image-preview"
    assert result.provider_response_id == "gemini-response-1"
    assert result.provider_request_json == {
        "model": "gemini-3.1-flash-image-preview",
        "prompt": "generate product image",
        "size": "2048x1152",
        "reference_image_count": 1,
        "reference_images": [{"filename": "ref.png", "mime_type": "image/png", "byte_count": len(image_bytes)}],
        "image_config": {"aspect_ratio": "16:9", "image_size": "2K", "output_mime_type": "image/png"},
    }
    assert result.provider_output_json == {
        "response_id": "gemini-response-1",
        "model_version": "gemini-test-version",
        "text_part_count": 1,
        "_productflow": {
            "model": "gemini-3.1-flash-image-preview",
            "requested_size": "2048x1152",
            "effective_aspect_ratio": "16:9",
            "effective_image_size": "2K",
        },
    }
    assert "base64" not in str(result.provider_request_json).lower()
    assert image_bytes.hex() not in str(result.provider_output_json)


def test_google_gemini_size_mapping_and_sanitized_errors() -> None:
    config = map_productflow_size_to_gemini_image_config("1024x1536", "gemini-2.5-flash-image")
    assert config.aspect_ratio == "2:3"
    assert config.image_size is None

    preview_config = map_productflow_size_to_gemini_image_config("3840x2160", "gemini-3-pro-image-preview")
    assert preview_config.aspect_ratio == "16:9"
    assert preview_config.image_size == "4K"

    with pytest.raises(RuntimeError) as missing_key:
        GoogleGeminiImageClient(
            ResolvedImageProviderConfig(
                provider_kind="google_gemini_image",
                model="gemini-2.5-flash-image",
                api_key=None,
            )
        ).generate_image(prompt="generated image", size="1024x1024")
    assert str(missing_key.value) == "Image provider profile is missing an API key"


def test_openai_images_client_retries_generate_without_optional_fields(
    configured_env: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("IMAGE_PROVIDER_KIND", "openai_images")
    monkeypatch.setenv("IMAGE_API_KEY", "demo-api-key")
    monkeypatch.setenv("IMAGE_GENERATE_MODEL", "gpt-image-1")
    monkeypatch.setenv("IMAGE_IMAGES_QUALITY", "high")
    monkeypatch.setenv("IMAGE_IMAGES_STYLE", "natural")
    get_settings.cache_clear()

    calls: list[dict] = []
    encoded_result = _make_demo_image_data_url().split(",", maxsplit=1)[1]

    class DummyImages:
        def generate(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError("unsupported optional field")
            return DummyImagesAPIResponse(encoded_result)

    class DummyOpenAI:
        def __init__(self, **kwargs) -> None:
            self.images = DummyImages()

    monkeypatch.setattr("productflow_backend.infrastructure.image.images_provider.OpenAI", DummyOpenAI)

    from productflow_backend.infrastructure.image.images_provider import OpenAIImagesClient

    result = OpenAIImagesClient().generate(prompt="fallback", size="1024x1024")[0]

    assert len(calls) == 2
    assert "quality" in calls[0]
    assert "style" in calls[0]
    assert "quality" not in calls[1]
    assert "style" not in calls[1]
    assert result.provider_output_json["_productflow"]["notes"] == [
        {"kind": "fallback", "message": "Provider does not support some optional parameters; completed with base parameters."}
    ]
    assert "unsupported optional field" not in str(result.provider_output_json)


def test_openai_images_client_edit_sends_multiple_images_and_falls_back_to_base_image(
    configured_env: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("IMAGE_PROVIDER_KIND", "openai_images")
    monkeypatch.setenv("IMAGE_API_KEY", "demo-api-key")
    monkeypatch.setenv("IMAGE_GENERATE_MODEL", "gpt-image-1")
    monkeypatch.setenv("IMAGE_IMAGES_QUALITY", "high")
    get_settings.cache_clear()

    calls: list[dict] = []
    encoded_result = _make_demo_image_data_url().split(",", maxsplit=1)[1]

    class DummyImages:
        def edit(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError("multiple files are not supported")
            return DummyImagesAPIResponse(encoded_result)

    class DummyOpenAI:
        def __init__(self, **kwargs) -> None:
            self.images = DummyImages()

    monkeypatch.setattr("productflow_backend.infrastructure.image.images_provider.OpenAI", DummyOpenAI)

    from productflow_backend.infrastructure.image.images_provider import ImagesReferenceImage, OpenAIImagesClient

    result = OpenAIImagesClient().edit(
        image=[
            ImagesReferenceImage(_make_demo_image_bytes(), "image/png", "base.png"),
            ImagesReferenceImage(_make_demo_image_bytes(), "image/png", "ref.png"),
        ],
        prompt="edit image",
        size="1024x1024",
    )[0]

    assert len(calls) == 2
    assert isinstance(calls[0]["image"], list)
    assert [image.name for image in calls[0]["image"]] == ["base.png", "ref.png"]
    assert calls[0]["quality"] == "high"
    assert calls[1]["image"].name == "base.png"
    assert "quality" not in calls[1]
    assert result.provider_request_json == {
        "model": "gpt-image-1",
        "prompt": "edit image",
        "size": "1024x1024",
        "n": 1,
        "image_count": 1,
        "images": [{"filename": "base.png", "mime_type": "image/png"}],
        "has_mask": False,
    }
    assert result.provider_output_json["_productflow"] == {
        "notes": [
            {"kind": "fallback", "message": "Provider does not support some optional parameters; completed with base parameters."},
            {"kind": "multi_image_fallback", "message": "Provider does not support multiple edit inputs; completed using only the base image."},
        ],
        "requested_image_count": 2,
        "effective_image_count": 1,
    }
    assert "multiple files" not in str(result.provider_output_json)


def test_openai_images_client_reports_missing_output_and_sanitizes_failures(
    configured_env: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("IMAGE_PROVIDER_KIND", "openai_images")
    monkeypatch.setenv("IMAGE_BASE_URL", "https://secret-provider.example/v1")
    monkeypatch.setenv("IMAGE_API_KEY", "sk-sensitive")
    monkeypatch.setenv("IMAGE_GENERATE_MODEL", "gpt-image-1")
    get_settings.cache_clear()

    class MissingOutputImages:
        def generate(self, **kwargs):
            return DummyImagesAPIResponse(None)

    class FailingImages:
        def generate(self, **kwargs):
            raise RuntimeError(f"raw failure with {kwargs}")

    class MissingOutputOpenAI:
        def __init__(self, **kwargs) -> None:
            self.images = MissingOutputImages()

    class FailingOpenAI:
        def __init__(self, **kwargs) -> None:
            self.images = FailingImages()

    from productflow_backend.infrastructure.image import images_provider
    from productflow_backend.infrastructure.image.images_provider import OpenAIImagesClient

    monkeypatch.setattr(images_provider, "OpenAI", MissingOutputOpenAI)
    with pytest.raises(RuntimeError) as missing_error:
        OpenAIImagesClient().generate(prompt="no image", size="1024x1024")
    assert str(missing_error.value) == "Image provider did not return any image; please retry later"

    monkeypatch.setattr(images_provider, "OpenAI", FailingOpenAI)
    with pytest.raises(RuntimeError) as failure_error:
        OpenAIImagesClient().generate(prompt="failed", size="1024x1024")
    assert str(failure_error.value) == "Image provider request failed; check provider configuration and retry"
    assert "sk-sensitive" not in str(failure_error.value)
    assert "secret-provider" not in str(failure_error.value)


def test_openai_images_poster_provider_uses_existing_prompt_contract_and_references(
    configured_env: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("IMAGE_PROVIDER_KIND", "openai_images")
    monkeypatch.setenv("IMAGE_API_KEY", "demo-api-key")
    monkeypatch.setenv("IMAGE_GENERATE_MODEL", "gpt-image-1")
    get_settings.cache_clear()

    calls: list[dict] = []
    encoded_result = _make_demo_image_data_url().split(",", maxsplit=1)[1]

    class DummyImages:
        def edit(self, **kwargs):
            calls.append(kwargs)
            return DummyImagesAPIResponse(encoded_result)

    class DummyOpenAI:
        def __init__(self, **kwargs) -> None:
            self.images = DummyImages()

    monkeypatch.setattr("productflow_backend.infrastructure.image.images_provider.OpenAI", DummyOpenAI)

    session = get_session_factory()()
    try:
        session.add_all(
            [
                AppSetting(
                    key="prompt_poster_image_edit_template",
                    value=(
                        "EDIT {product_name}/{category}/{price}/{source_note}/{instruction}/"
                        "{kind_label}/{size}/{context_block}/{reference_policy}/{kind_requirements}"
                    ),
                ),
                AppSetting(key="prompt_poster_image_reference_policy", value="keepproductsubject"),
            ]
        )
        session.commit()
    finally:
        session.close()

    source_path = configured_env / "images-source.png"
    reference_path = configured_env / "images-reference.png"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(_make_demo_image_bytes())
    reference_path.write_bytes(_make_demo_image_bytes())

    generated_image, model_name = OpenAIImagesImageProvider().generate_poster_image(
        poster=PosterGenerationInput(
            copy_prompt_mode="image_edit",
            product_name="test product",
            category="test category",
            price="9.90",
            source_note="waterproof Oxford fabric",
            instruction="cleaner background",
            image_size="1024x1024",
            source_image=source_path,
            reference_images=[
                ReferenceImageInput(
                    path=reference_path,
                    mime_type="image/png",
                    filename="reference.png",
                )
            ],
        ),
        kind=PosterKind.MAIN_IMAGE,
    )

    assert generated_image.mime_type == "image/png"
    assert model_name == "gpt-image-1"
    payload = calls[0]
    assert payload["model"] == "gpt-image-1"
    assert payload["size"] == "1024x1024"
    assert [image.name for image in payload["image"]] == ["images-source.png", "reference.png"]
    prompt = payload["prompt"]
    assert "EDIT test product/test category/9.90/waterproof Oxford fabric/cleaner background/Main image/1024x1024" in prompt
    assert "- Notes: waterproof Oxford fabric" in prompt
    assert "- Number of reference images: 2" in prompt
    assert "- Product source image: the first input image" in prompt
    assert "- Reference image: reference.png (role: Reference image)" in prompt
    assert "keepproductsubject" in prompt
    assert "Do not draw field names, tag names, JSON keys" in prompt


def test_openai_images_poster_provider_batches_count_as_images_api_n(
    configured_env: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("IMAGE_PROVIDER_KIND", "openai_images")
    monkeypatch.setenv("IMAGE_API_KEY", "demo-api-key")
    monkeypatch.setenv("IMAGE_GENERATE_MODEL", "gpt-image-1")
    get_settings.cache_clear()

    calls: list[dict] = []
    encoded_result = _make_demo_image_data_url().split(",", maxsplit=1)[1]

    class DummyImages:
        def generate(self, **kwargs):
            calls.append(kwargs)
            return DummyImagesAPIResponse(b64_jsons=[encoded_result, encoded_result, encoded_result])

    class DummyOpenAI:
        def __init__(self, **kwargs) -> None:
            self.images = DummyImages()

    monkeypatch.setattr("productflow_backend.infrastructure.image.images_provider.OpenAI", DummyOpenAI)

    generated_images = OpenAIImagesImageProvider().generate_poster_images(
        poster=PosterGenerationInput(
            product_name="batchproduct",
            instruction="generate candidate",
            image_size="1024x1024",
            tool_options={"quality": "high", "n": 1},
        ),
        kind=PosterKind.MAIN_IMAGE,
        count=3,
    )

    assert len(calls) == 1
    assert calls[0]["n"] == 3
    assert calls[0]["quality"] == "high"
    assert len(generated_images) == 3
    assert [generated_image.variant_label for generated_image, _ in generated_images] == ["v1", "v2", "v3"]
    assert [model_name for _, model_name in generated_images] == ["gpt-image-1", "gpt-image-1", "gpt-image-1"]


def test_generated_poster_mode_uses_image_provider(
    db_session,
    configured_env: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("POSTER_GENERATION_MODE", "generated")
    monkeypatch.setenv("IMAGE_PROVIDER_KIND", "mock")
    get_settings.cache_clear()

    product = create_product(
        db_session,
        name="portable juicer",
        category="small appliance",
        price="89.00",
        source_note=None,
        image_bytes=_make_demo_image_bytes(),
        filename="juicer.png",
        content_type="image/png",
        reference_image_uploads=[
            (_make_demo_image_bytes(), "ref-1.png", "image/png"),
            (_make_demo_image_bytes(), "ref-2.png", "image/png"),
        ],
    )

    run_product_workflow(db_session, product_id=product.id)
    db_session.expire_all()

    product_after_poster = get_product_detail(db_session, product.id)
    assert product_after_poster.poster_variants
    assert all(
        "workflow:mock:mock-generated-r1:mock-image-v1" in poster.template_name
        for poster in product_after_poster.poster_variants
    )

def test_default_image_prompts_are_low_pollution_context_carriers(configured_env: Path) -> None:
    from productflow_backend.infrastructure.image.chat_service import ImageChatService

    prompt = OpenAIResponsesImageProvider()._build_prompt(
        PosterGenerationInput(
            copy_prompt_mode="image_edit",
            product_name="",
            instruction="paint a blue abstract gradient",
            image_size="1280x720",
        ),
        PosterKind.MAIN_IMAGE,
        "1280x720",
    )
    chat_prompt = ImageChatService()._build_prompt("paint a blue abstract gradient", [], "1280x720")

    forbidden = ["ecommerceposter", "inherit inputreference image", "productsubject", "main title", "selling point", "CTA", "price label"]
    assert all(term not in prompt for term in forbidden)
    assert all(term not in chat_prompt for term in ["already inherited fixed", "subject", "composition and material"])
    assert "should not inject" not in prompt
    assert "paint a blue abstract gradient" in prompt
    assert "No explicit upstream context" in prompt
    assert "1280x720" in chat_prompt


def test_openai_image_prompt_uses_structured_copy_context(configured_env: Path) -> None:
    prompt = OpenAIResponsesImageProvider()._build_prompt(
        PosterGenerationInput(
            product_name="structuredproduct",
            instruction="highlight structured context",
            structured_copy_context="summary：structuredmain title\nbody：structured body\nselling point：structuredselling point",
        ),
        PosterKind.MAIN_IMAGE,
        "1024x1024",
    )

    assert "structuredmain title" in prompt
    assert "structured body" in prompt
    assert "structuredselling point" in prompt
    assert "Available copy references" in prompt
    assert "do not draw field names, tag names, or context descriptions" in prompt
    assert "should not be primary input" not in prompt


def test_openai_responses_image_client_reports_completed_text_without_image(
    configured_env: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("IMAGE_PROVIDER_KIND", "openai_responses")
    monkeypatch.setenv("IMAGE_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("IMAGE_API_KEY", "demo-api-key")
    monkeypatch.setenv("IMAGE_GENERATE_MODEL", "gpt-5.4")
    get_settings.cache_clear()

    class DummyResponse:
        id = "resp_text_only"
        status = "completed"
        output = [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "sorry，cannot generate thisimageimage。"}],
            }
        ]

        def model_dump(self, *, mode: str, exclude_none: bool) -> dict:
            return {"id": self.id, "status": self.status, "output": self.output}

    class DummyResponses:
        def create(self, **kwargs):
            return DummyResponse()

    class DummyOpenAI:
        def __init__(self, **kwargs) -> None:
            self.responses = DummyResponses()

    monkeypatch.setattr("productflow_backend.infrastructure.image.responses_provider.OpenAI", DummyOpenAI)

    from productflow_backend.infrastructure.image.responses_provider import OpenAIResponsesImageClient

    with pytest.raises(RuntimeError) as error:
        OpenAIResponsesImageClient().generate_image(prompt="returns text only", size="1024x1024")

    assert str(error.value) == "Image provider completed the request but returned text instead of an image"
