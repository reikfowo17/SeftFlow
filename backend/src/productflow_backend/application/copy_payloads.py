from __future__ import annotations

from typing import Any

from productflow_backend.application.contracts import (
    BlocksCopyContent,
    CopyNodeConfigV2,
    CopyPayloadV2,
    CopySlotRequest,
    FreeformCopyContent,
    LayoutBriefCopyContent,
)
from productflow_backend.infrastructure.db.models import CopySet


def normalize_copy_node_config(raw_config: dict[str, Any] | None) -> CopyNodeConfigV2:
    config = raw_config or {}
    instruction = _string_or_empty(config.get("instruction"))
    output_mode = _string_or_none(config.get("output_mode")) or _infer_output_mode(instruction)
    requested_slots = config.get("requested_slots")
    if requested_slots is not None and not isinstance(requested_slots, list):
        raise ValueError("Copy requested_slots must be an array")
    return CopyNodeConfigV2.model_validate(
        {
            "version": 2,
            "instruction": instruction,
            "purpose": _string_or_none(config.get("purpose")),
            "channel": _string_or_none(config.get("channel")),
            "tone": _string_or_none(config.get("tone")),
            "output_mode": output_mode,
            "requested_slots": normalize_copy_slot_requests(requested_slots) if requested_slots is not None else [],
        }
    )


def normalize_copy_slot_requests(raw_slots: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_slots, list):
        raise ValueError("Copy requested_slots must be an array")
    normalized: list[dict[str, Any]] = []
    for index, raw_slot in enumerate(raw_slots, start=1):
        if isinstance(raw_slot, CopySlotRequest):
            normalized.append(raw_slot.model_dump(mode="json"))
            continue
        if isinstance(raw_slot, str):
            label = raw_slot.strip()
            if not label:
                raise ValueError("Copy requested_slots array item must not be empty")
            normalized.append({"key": f"slot_{index}", "label": label, "required": False, "hint": None})
            continue
        if isinstance(raw_slot, dict):
            normalized.append(CopySlotRequest.model_validate(raw_slot).model_dump(mode="json"))
            continue
        raise ValueError("Copy requested_slots Array must be objects or text")
    return normalized


def normalize_copy_payload(raw_payload: Any, *, fallback_purpose: str | None = None) -> CopyPayloadV2:
    if isinstance(raw_payload, CopyPayloadV2):
        return raw_payload
    if not isinstance(raw_payload, dict):
        raise ValueError("Copy model output must be a JSON object")
    if raw_payload.get("version") == 2 or "content" in raw_payload:
        payload = _normalize_v2_payload_dict(raw_payload)
        if fallback_purpose and not payload.get("purpose"):
            payload["purpose"] = fallback_purpose
        return CopyPayloadV2.model_validate(payload)
    raise ValueError("Copy model output must conform to the CopyPayloadV2 contract")


def copy_set_structured_payload(copy_set: CopySet) -> CopyPayloadV2:
    if isinstance(copy_set.structured_payload, dict):
        return normalize_copy_payload(copy_set.structured_payload)
    raise ValueError("Copy version is missing structured_payload")


def copy_payload_context_text(payload: CopyPayloadV2) -> str:
    parts = [f"summary: {payload.summary}"]
    if payload.purpose:
        parts.append(f"purpose: {payload.purpose}")
    if isinstance(payload.content, FreeformCopyContent):
        parts.append(f"body: {payload.content.text}")
    elif isinstance(payload.content, BlocksCopyContent):
        for block in payload.content.blocks:
            prefix = " / ".join(part for part in (block.label, block.role) if part)
            body = block.text
            if block.note:
                body = f"{body} ({block.note}) "
            if block.visual_hint:
                body = f"{body}; visual suggestion: {block.visual_hint}"
            parts.append(f"{prefix}: {body}" if prefix else body)
    elif isinstance(payload.content, LayoutBriefCopyContent):
        for section in payload.content.sections:
            title = f"{section.title}: " if section.title else ""
            body = section.body or ""
            item_text = "; ".join(
                f"{item.label or item.role or 'entry'}: {item.text}" for item in section.items
            )
            visual = f"; visual suggestion: {section.visual_hint}" if section.visual_hint else ""
            parts.append(f"{title}{body}{('; ' + item_text) if item_text else ''}{visual}")
    if payload.visual_guidance:
        guidance = payload.visual_guidance
        if guidance.main_message:
            parts.append(f"primary information: {guidance.main_message}")
        if guidance.composition_hint:
            parts.append(f"compositionsuggestion: {guidance.composition_hint}")
        if guidance.hierarchy:
            parts.append(f"Information hierarchy: {' > '.join(guidance.hierarchy)}")
        if guidance.avoid:
            parts.append(f"avoid: {', '.join(guidance.avoid)}")
    return "\n".join(part for part in parts if part.strip())


def copy_payload_to_output(payload: CopyPayloadV2) -> dict[str, Any]:
    return {
        "structured_payload": payload.model_dump(mode="json"),
        "summary": f"Copy: {payload.summary}",
    }


def _normalize_v2_payload_dict(raw_payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(raw_payload)
    payload.setdefault("version", 2)
    visual_guidance = _string_or_none(payload.get("visual_guidance"))
    if visual_guidance:
        payload["visual_guidance"] = {"composition_hint": visual_guidance}
    content = payload.get("content")
    if not isinstance(content, dict):
        return payload
    content = dict(content)
    if content.get("type") and not content.get("kind"):
        content["kind"] = content["type"]
    if content.get("kind") == "freeform":
        content["text"] = _freeform_text_from_content(content)
    if content.get("kind") == "blocks":
        blocks = content.get("blocks")
        if isinstance(blocks, list):
            normalized_blocks = [
                _normalize_block_dict(block, fallback_id=f"block-{index}", index=index)
                for index, block in enumerate(blocks, start=1)
            ]
            content["blocks"] = [block for block in normalized_blocks if _normalized_block_has_text(block)]
    elif content.get("kind") == "layout_brief":
        sections = content.get("sections")
        if isinstance(sections, list):
            content["sections"] = [
                _normalize_section_dict(section, fallback_id=f"section-{index}", index=index)
                for index, section in enumerate(sections, start=1)
            ]
        else:
            items = content.get("items")
            if isinstance(items, list):
                content["sections"] = [
                    _normalize_section_dict(section, fallback_id=f"section-{index}", index=index)
                    for index, section in enumerate(items, start=1)
                ]
            else:
                sections = _layout_sections_from_object(content)
                if sections:
                    content["sections"] = sections
    payload["content"] = content
    return payload


def _normalize_section_dict(raw_section: Any, *, fallback_id: str, index: int) -> Any:
    if not isinstance(raw_section, dict):
        return raw_section
    section = dict(raw_section)
    role = _string_or_none(section.get("type") or section.get("role"))
    title = _string_or_none(
        section.get("title") or section.get("label") or section.get("heading") or section.get("angle")
    )
    if title and not section.get("title"):
        section["title"] = title
    body = _section_body_text(section)
    if body and not section.get("body"):
        section["body"] = body
    visual_hint = _string_or_none(
        section.get("visual_hint")
        or section.get("visual")
        or section.get("visual_suggestion")
        or section.get("visual_expression")
        or section.get("composition")
        or section.get("shot")
    )
    if visual_hint and not section.get("visual_hint"):
        section["visual_hint"] = visual_hint
    section.setdefault("id", _slug_id(role or section.get("title"), fallback_id=fallback_id, index=index))
    items = section.get("items")
    if isinstance(items, list):
        normalized_items = [
            _normalize_block_dict(item, fallback_id=f"{section['id']}-item-{item_index}", index=item_index)
            for item_index, item in enumerate(items, start=1)
        ]
        section["items"] = [item for item in normalized_items if _normalized_block_has_text(item)]
    return section


def _layout_sections_from_object(content: dict[str, Any]) -> list[Any]:
    ignored_keys = {"kind", "type"}
    sections = []
    for index, (key, value) in enumerate(
        ((key, value) for key, value in content.items() if key not in ignored_keys),
        start=1,
    ):
        title = _humanize_key(key)
        if isinstance(value, dict):
            section = dict(value)
            section.setdefault("title", title)
        elif isinstance(value, list) and any(isinstance(item, dict) for item in value):
            section = {"title": title, "items": value}
        else:
            section = {"title": title, "body": _text_from_any(value)}
        normalized = _normalize_section_dict(section, fallback_id=f"section-{index}", index=index)
        if isinstance(normalized, dict) and (
            _string_or_none(normalized.get("title"))
            or _string_or_none(normalized.get("body"))
            or normalized.get("items")
            or _string_or_none(normalized.get("visual_hint"))
        ):
            sections.append(normalized)
    return sections


def _normalize_block_dict(raw_block: Any, *, fallback_id: str, index: int) -> Any:
    if not isinstance(raw_block, dict):
        return raw_block
    block = dict(raw_block)
    role = _string_or_none(block.get("role") or block.get("type"))
    if role and not block.get("role"):
        block["role"] = role
    if not block.get("label"):
        block["label"] = _string_or_none(block.get("tag") or block.get("title") or block.get("name") or role)
    block.setdefault(
        "id",
        _slug_id(block.get("id") or block.get("label") or role, fallback_id=fallback_id, index=index),
    )
    if not _string_or_none(block.get("text")):
        block["text"] = _block_text_from_items(block.get("items")) or _string_or_empty(
            block.get("content") or block.get("description") or block.get("copy")
        )
    if block.get("items") is not None and not block.get("note"):
        item_text = _block_text_from_items(block.get("items"))
        if item_text:
            block["note"] = item_text
    return block


def _normalized_block_has_text(block: Any) -> bool:
    return not isinstance(block, dict) or bool(_string_or_none(block.get("text")))


def _freeform_text_from_content(content: dict[str, Any]) -> str:
    text = _text_from_any(content.get("text"))
    if text:
        return text
    for key in ("items", "content", "copy", "body", "description", "paragraphs", "lines", "hooks"):
        text = _text_from_any(content.get(key))
        if text:
            return text
    ignored_keys = {"kind", "type"}
    keyed_parts = []
    for key, value in content.items():
        if key in ignored_keys:
            continue
        text = _text_from_any(value)
        if text:
            keyed_parts.append(f"{key}: {text}")
    return "\n".join(keyed_parts)


def _section_body_text(section: dict[str, Any]) -> str:
    for key in (
        "body",
        "copy",
        "text",
        "description",
        "reason",
        "message",
        "content",
        "note",
        "caption",
        "subtitle",
    ):
        text = _text_from_any(section.get(key))
        if text:
            return text
    return ""


def _block_text_from_items(items: Any) -> str:
    return _text_from_any(items, pair_separator="; ")


def _text_from_any(value: Any, *, pair_separator: str = "\n") -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            text = _text_from_any(item, pair_separator=pair_separator)
            if text:
                parts.append(text)
        return pair_separator.join(parts)
    if isinstance(value, dict):
        direct = _string_or_empty(
            value.get("text")
            or value.get("content")
            or value.get("description")
            or value.get("copy")
            or value.get("body")
            or value.get("label")
        )
        if direct:
            return direct
        parts = []
        for key, item in value.items():
            text = _text_from_any(item, pair_separator=pair_separator)
            if text:
                parts.append(f"{key}: {text}")
        return pair_separator.join(parts)
    return ""


def _slug_id(value: Any, *, fallback_id: str, index: int) -> str:
    text = _string_or_none(value)
    if not text:
        return fallback_id
    allowed = [character.lower() for character in text if character.isalnum() or character in {"-", "_"}]
    slug = "".join(allowed).strip("-_")
    return f"{slug or fallback_id}-{index}"


def _humanize_key(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").strip() or "section"


def _infer_output_mode(instruction: str) -> str:
    if any(keyword in instruction for keyword in ("hierarchy", "layout", "whitespace", "composition", "infographic")):
        return "layout_brief"
    if any(keyword in instruction for keyword in ("Steps", "Specifications", "Selling points", "Checklist", "Comparison", "Tag")):
        return "blocks"
    return "freeform"


def _string_or_empty(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _string_or_none(value: Any) -> str | None:
    value = value.strip() if isinstance(value, str) else ""
    return value or None
