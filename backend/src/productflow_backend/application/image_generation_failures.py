from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

ImageGenerationFailureCategory = Literal[
    "rate_limit",
    "quota",
    "content_policy",
    "connection",
    "timeout",
    "provider_5xx",
    "unsupported_parameters",
    "bad_request",
    "unknown",
]
ImageGenerationFailureRetryHint = Literal["retry_later", "revise_input", "check_settings"]


@dataclass(frozen=True, slots=True)
class ImageGenerationFailureDecision:
    reason: str
    retryable: bool
    retry_hint: ImageGenerationFailureRetryHint
    category: ImageGenerationFailureCategory


_SENSITIVE_FAILURE_PATTERNS = (
    re.compile(r"sk-[a-zA-Z0-9_-]+"),
    re.compile(r"\b(api[_ -]?key|token|bearer|authorization|credential|secret)\b", re.IGNORECASE),
    re.compile(r"\b(base_url|prompt)\s*=", re.IGNORECASE),
    re.compile(r"https?://", re.IGNORECASE),
    re.compile(r"(/tmp/|traceback|stack trace)", re.IGNORECASE),
)

_NON_RETRYABLE_FAILURE_RULES: tuple[
    tuple[ImageGenerationFailureCategory, ImageGenerationFailureRetryHint, str, tuple[re.Pattern[str], ...]],
    ...,
] = (
    (
        "content_policy",
        "revise_input",
        "The image provider rejected this content or safety policy; adjust the prompt or reference image and retry",
        (
            re.compile(r"content policy|safety|moderation|policy violation|blocked|refused", re.IGNORECASE),
            re.compile(r"safety policy|content policy|violation|sensitive content"),
        ),
    ),
    (
        "unsupported_parameters",
        "check_settings",
        "Image provider parameters are not supported; please check size, model, or advanced parameters and retry",
        (
            re.compile(
                r"unsupported|unknown parameter|unrecognized|unexpected|not supported|not_support",
                re.IGNORECASE,
            ),
            re.compile(r"not supported|invalid|invalid|parameters"),
        ),
    ),
    (
        "bad_request",
        "revise_input",
        "Image provider rejected this request; please adjust prompt, reference image, or parameters and retry",
        (
            re.compile(r"\bbad request\b", re.IGNORECASE),
            re.compile(r"reject(ed)?|refus(ed|al)|denied", re.IGNORECASE),
            re.compile(r"Request was rejected|rejected the request"),
        ),
    ),
)

_RETRYABLE_FAILURE_RULES: tuple[
    tuple[ImageGenerationFailureCategory, ImageGenerationFailureRetryHint, str, tuple[re.Pattern[str], ...]],
    ...,
] = (
    (
        "rate_limit",
        "retry_later",
        "Image provider rate-limited or out of quota; retry later or lower concurrency",
        (
            re.compile(r"\b429\b"),
            re.compile(r"\brate[ _-]?limit(ed)?\b", re.IGNORECASE),
            re.compile(r"too many requests", re.IGNORECASE),
            re.compile(r"rate-limited|frequency"),
        ),
    ),
    (
        "quota",
        "retry_later",
        "Image provider rate-limited or out of quota; retry later or lower concurrency",
        (
            re.compile(r"quota|insufficient_quota", re.IGNORECASE),
            re.compile(r"quota"),
        ),
    ),
    (
        "connection",
        "retry_later",
        "Image provider connection interrupted; check network or proxy and retry",
        (
            re.compile(r"connection reset|connection aborted|connection error|remote disconnected", re.IGNORECASE),
            re.compile(r"broken pipe|econnreset|network is unreachable|connection refused", re.IGNORECASE),
            re.compile(r"stream interrupted|Connection interrupted|connectionfailed|Network unreachable"),
        ),
    ),
    (
        "timeout",
        "retry_later",
        "Image provider request timed out; please retry later",
        (
            re.compile(r"timeout|timed out|read timeout|connect timeout", re.IGNORECASE),
            re.compile(r"timeout"),
        ),
    ),
    (
        "provider_5xx",
        "retry_later",
        "Image provider service exception; please retry later",
        (
            re.compile(r"\b5\d\d\b"),
            re.compile(r"server error|internal error|bad gateway|service unavailable|gateway timeout", re.IGNORECASE),
            re.compile(r"Service exception|Service unavailable"),
        ),
    ),
)
_UNSUPPORTED_PARAMETER_PATTERNS = (
    re.compile(r"unsupported|invalid|bad request|\b400\b|unknown parameter|unrecognized|unexpected", re.IGNORECASE),
    re.compile(r"not supported|invalid|invalid|parameters|size"),
)
_ACTIONABLE_SIZE_DETAIL_PATTERNS = (
    re.compile(r"\d+\s*[x×]\s*\d+"),
    re.compile(r"minimum|maximum|min|max|size|size", re.IGNORECASE),
)
_EXPLICIT_REJECT_PATTERNS = (
    re.compile(r"reject(ed)?|refus(ed|al)|denied|blocked", re.IGNORECASE),
    re.compile(r"rejected|blocked"),
)


def _iter_exception_chain(exc: BaseException) -> Iterable[BaseException]:
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _iter_exception_diagnostics(exc: BaseException) -> Iterable[str]:
    for current in _iter_exception_chain(exc):
        parts = [type(current).__name__, str(current)]
        status_code = getattr(current, "status_code", None)
        if status_code is not None:
            parts.append(str(status_code))
        code = getattr(current, "code", None)
        if code is not None:
            parts.append(str(code))
        response = getattr(current, "response", None)
        response_status_code = getattr(response, "status_code", None)
        if response_status_code is not None:
            parts.append(str(response_status_code))
        body = getattr(current, "body", None)
        if body is not None:
            parts.append(str(body))
        yield " ".join(part for part in parts if part)


def _iter_exception_display_messages(exc: BaseException) -> Iterable[str]:
    for current in _iter_exception_chain(exc):
        message = " ".join(str(current).strip().split())
        if message:
            yield message


def _contains_sensitive_material(message: str) -> bool:
    return any(pattern.search(message) for pattern in _SENSITIVE_FAILURE_PATTERNS)


def _decision(
    *,
    reason: str,
    retryable: bool,
    retry_hint: ImageGenerationFailureRetryHint,
    category: ImageGenerationFailureCategory,
) -> ImageGenerationFailureDecision:
    return ImageGenerationFailureDecision(
        reason=reason,
        retryable=retryable,
        retry_hint=retry_hint,
        category=category,
    )


def _categorized_failure_decision(messages: list[str]) -> ImageGenerationFailureDecision | None:
    haystack = " ".join(messages)
    for category, retry_hint, reason, patterns in _NON_RETRYABLE_FAILURE_RULES:
        if any(pattern.search(haystack) for pattern in patterns):
            return _decision(reason=reason, retryable=False, retry_hint=retry_hint, category=category)
    for category, retry_hint, reason, patterns in _RETRYABLE_FAILURE_RULES:
        if any(pattern.search(haystack) for pattern in patterns):
            return _decision(reason=reason, retryable=True, retry_hint=retry_hint, category=category)
    return None


def _uncategorized_display_decision(
    *,
    raw_message: str,
    diagnostics: list[str],
    generic_message: str,
) -> ImageGenerationFailureDecision:
    diagnostics_text = " ".join(diagnostics)
    if _contains_sensitive_material(raw_message):
        return _decision(reason=generic_message, retryable=True, retry_hint="retry_later", category="unknown")
    if all(pattern.search(raw_message) for pattern in _ACTIONABLE_SIZE_DETAIL_PATTERNS):
        return _decision(
            reason=f"Image generation failed: {raw_message[:300]}",
            retryable=False,
            retry_hint="check_settings",
            category="unsupported_parameters",
        )
    if any(pattern.search(diagnostics_text) for pattern in _UNSUPPORTED_PARAMETER_PATTERNS):
        return _decision(
            reason="Image provider parameters are not supported; please check size, model, or advanced parameters and retry",
            retryable=False,
            retry_hint="check_settings",
            category="unsupported_parameters",
        )
    if any(pattern.search(diagnostics_text) for pattern in _EXPLICIT_REJECT_PATTERNS):
        return _decision(
            reason="Image provider rejected this request; please adjust prompt, reference image, or parameters and retry",
            retryable=False,
            retry_hint="revise_input",
            category="bad_request",
        )
    return _decision(
        reason=f"Image generation failed: {raw_message[:300]}",
        retryable=True,
        retry_hint="retry_later",
        category="unknown",
    )


def classify_image_generation_failure(
    exc: BaseException,
    *,
    generic_message: str,
) -> ImageGenerationFailureDecision:
    diagnostics = [" ".join(message.strip().split()) for message in _iter_exception_diagnostics(exc) if message.strip()]
    display_messages = list(_iter_exception_display_messages(exc))
    if not diagnostics and not display_messages:
        return _decision(reason=generic_message, retryable=True, retry_hint="retry_later", category="unknown")
    categorized_decision = _categorized_failure_decision(diagnostics)
    raw_message = display_messages[0] if display_messages else diagnostics[0]
    if not raw_message:
        return _decision(reason=generic_message, retryable=True, retry_hint="retry_later", category="unknown")
    if not _contains_sensitive_material(raw_message) and all(
        pattern.search(raw_message) for pattern in _ACTIONABLE_SIZE_DETAIL_PATTERNS
    ):
        return _decision(
            reason=f"Image generation failed: {raw_message[:300]}",
            retryable=False,
            retry_hint="check_settings",
            category="unsupported_parameters",
        )
    if categorized_decision is not None:
        return categorized_decision
    return _uncategorized_display_decision(
        raw_message=raw_message,
        diagnostics=diagnostics,
        generic_message=generic_message,
    )


def safe_image_generation_failure_reason(exc: BaseException, *, generic_message: str) -> str:
    return classify_image_generation_failure(exc, generic_message=generic_message).reason
