from __future__ import annotations

from productflow_backend.infrastructure.image.base import ImageProvider
from productflow_backend.infrastructure.image.gemini_provider import GoogleGeminiImageProvider
from productflow_backend.infrastructure.image.images_provider import OpenAIImagesImageProvider
from productflow_backend.infrastructure.image.mock_provider import MockImageProvider
from productflow_backend.infrastructure.image.responses_provider import OpenAIResponsesImageProvider
from productflow_backend.infrastructure.provider_config import resolve_image_provider_config


def get_image_provider() -> ImageProvider:
    """ProviderImage generationProvider """
    provider_config = resolve_image_provider_config()
    if provider_config.provider_kind == "mock":
        return MockImageProvider()
    if provider_config.provider_kind == "openai_responses":
        return OpenAIResponsesImageProvider(provider_config)
    if provider_config.provider_kind == "openai_images":
        return OpenAIImagesImageProvider(provider_config)
    if provider_config.provider_kind == "google_gemini_image":
        return GoogleGeminiImageProvider(provider_config)
    raise RuntimeError(f"unsupportedImage provider: {provider_config.provider_kind}")
