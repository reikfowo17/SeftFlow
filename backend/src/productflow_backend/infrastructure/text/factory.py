from __future__ import annotations

from productflow_backend.infrastructure.provider_config import resolve_text_provider_config
from productflow_backend.infrastructure.text.base import TextProvider
from productflow_backend.infrastructure.text.mock_provider import MockTextProvider
from productflow_backend.infrastructure.text.openai_provider import OpenAITextProvider


def get_text_provider() -> TextProvider:
    """ProviderGenerate Provider """
    provider_config = resolve_text_provider_config()
    if provider_config.provider_kind == "openai":
        return OpenAITextProvider(provider_config)
    return MockTextProvider()
