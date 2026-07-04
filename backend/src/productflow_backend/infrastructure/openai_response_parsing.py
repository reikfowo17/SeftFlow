from __future__ import annotations

import json
from json import JSONDecodeError
from typing import Any


def read_json_object_from_response(response: object, *, error_label: str) -> dict[str, Any]:
    text = response_output_text(response)
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
    except JSONDecodeError as exc:
        extracted = extract_json_object_text(text)
        if extracted:
            payload = json.loads(extracted)
        else:
            snippet = text[:200] if text else "<empty>"
            raise ValueError(f"{error_label} did not return a JSON object: {snippet}") from exc
    if not isinstance(payload, dict):
        snippet = text[:200] if text else "<empty>"
        raise ValueError(f"{error_label} did not return a JSON object: {snippet}")
    return payload


def response_output_text(response: object) -> str:
    if isinstance(response, str):
        return (extract_sse_output_text(response) or response).strip()

    output_text = getattr(response, "output_text", "")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    model_dump = getattr(response, "model_dump", None)
    if callable(model_dump):
        try:
            payload = model_dump(mode="json")
        except TypeError:
            payload = model_dump()
        if isinstance(payload, dict):
            return extract_response_dict_text(payload).strip()

    return ""


def extract_json_object_text(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index, character in enumerate(text[start:], start=start):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def extract_sse_output_text(text: str) -> str | None:
    current_event: str | None = None
    chunks: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
            continue
        if not line.startswith("data:"):
            continue
        data_text = line.split(":", 1)[1].strip()
        if not data_text or data_text == "[DONE]":
            continue
        try:
            payload = json.loads(data_text)
        except JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        delta = payload.get("delta")
        is_output_text_delta = (
            current_event == "response.output_text.delta" or payload.get("type") == "response.output_text.delta"
        )
        if is_output_text_delta and isinstance(delta, str):
            chunks.append(delta)

    if chunks:
        return "".join(chunks)
    return None


def extract_response_dict_text(payload: dict[str, Any]) -> str:
    output = payload.get("output")
    if not isinstance(output, list):
        return ""
    chunks: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "".join(chunks)
