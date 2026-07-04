from __future__ import annotations

import pytest

from productflow_backend.application.image_generation_failures import (
    classify_image_generation_failure,
    safe_image_generation_failure_reason,
)

GENERIC = "Image generation failed, please retry later"


class ProviderStatusError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (
            ProviderStatusError("Rate limit reached for image generations", status_code=429),
            "Image provider rate-limited or out of quota; retry later or lower concurrency",
        ),
        (
            ProviderStatusError("Request blocked by content policy", status_code=400),
            "The image provider rejected this content or safety policy; adjust the prompt or reference image and retry",
        ),
        (
            ConnectionError("connection reset by peer"),
            "Image provider connection interrupted; check network or proxy and retry",
        ),
        (
            ProviderStatusError("invalid image size 64x64", status_code=400),
            "Image generation failed: invalid image size 64x64",
        ),
        (
            ProviderStatusError("upstream service unavailable", status_code=503),
            "Image provider service exception; please retry later",
        ),
    ],
)
def test_safe_image_generation_failure_reason_categorizes_common_provider_failures(
    exc: BaseException,
    reason: str,
) -> None:
    assert safe_image_generation_failure_reason(exc, generic_message=GENERIC) == reason


def test_safe_image_generation_failure_reason_uses_exception_chain() -> None:
    cause = ProviderStatusError("Too many requests", status_code=429)
    wrapped = RuntimeError("image provider request failed，please check provider configuration and retry")
    wrapped.__cause__ = cause

    assert safe_image_generation_failure_reason(wrapped, generic_message=GENERIC) == (
        "Image provider rate-limited or out of quota; retry later or lower concurrency"
    )


def test_safe_image_generation_failure_reason_keeps_sensitive_unknown_errors_generic() -> None:
    assert safe_image_generation_failure_reason(
        RuntimeError("provider failed sk-test-token base_url=https://secret.example/v1 prompt=full prompt"),
        generic_message=GENERIC,
    ) == GENERIC


@pytest.mark.parametrize(
    ("exc", "category", "retryable"),
    [
        (ProviderStatusError("Too many requests", status_code=429), "rate_limit", True),
        (ConnectionError("connection reset by peer"), "connection", True),
        (TimeoutError("read timeout"), "timeout", True),
        (ProviderStatusError("upstream service unavailable", status_code=503), "provider_5xx", True),
        (ProviderStatusError("Request blocked by content policy", status_code=400), "content_policy", False),
        (ProviderStatusError("unknown parameter: background", status_code=400), "unsupported_parameters", False),
        (ProviderStatusError("bad request: provider rejected input", status_code=400), "bad_request", False),
    ],
)
def test_classify_image_generation_failure_returns_retry_decision(
    exc: BaseException,
    category: str,
    retryable: bool,
) -> None:
    decision = classify_image_generation_failure(exc, generic_message=GENERIC)

    assert decision.category == category
    assert decision.retryable is retryable
    assert decision.reason


def test_classify_image_generation_failure_uses_wrapped_cause_for_retry_decision() -> None:
    cause = ProviderStatusError("Request blocked by safety policy", status_code=400)
    wrapped = RuntimeError("image provider request failed，please check provider configuration and retry")
    wrapped.__cause__ = cause

    decision = classify_image_generation_failure(wrapped, generic_message=GENERIC)

    assert decision.category == "content_policy"
    assert decision.retryable is False
    assert decision.retry_hint == "revise_input"


def test_classify_image_generation_failure_keeps_actionable_size_detail_non_retryable() -> None:
    decision = classify_image_generation_failure(
        RuntimeError("image2 not supported 64x64，minimum size is 512x512"),
        generic_message=GENERIC,
    )

    assert decision.reason == "Image generation failed: image2 not supported 64x64，minimum size is 512x512"
    assert decision.category == "unsupported_parameters"
    assert decision.retryable is False
    assert decision.retry_hint == "check_settings"
