from __future__ import annotations

from abc import ABC, abstractmethod

from productflow_backend.application.contracts import (
    CopyNodeConfigV2,
    CopyPayloadV2,
    CreativeBriefPayload,
    ProductInput,
    ReferenceImageInput,
)


class TextProvider(ABC):
    """Generate  Product(brief) + CopyGenerate (copy) """

    provider_name: str
    prompt_version: str = "v1"

    @abstractmethod
    def generate_brief(self, product: ProductInput) -> tuple[CreativeBriefPayload, str]:
        raise NotImplementedError

    @abstractmethod
    def generate_copy(
        self,
        product: ProductInput,
        brief: CreativeBriefPayload,
        config: CopyNodeConfigV2,
        reference_images: list[ReferenceImageInput] | None = None,
    ) -> tuple[CopyPayloadV2, str]:
        raise NotImplementedError
