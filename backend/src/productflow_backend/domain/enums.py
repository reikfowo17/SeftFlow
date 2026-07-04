from __future__ import annotations

from enum import StrEnum


class SourceAssetKind(StrEnum):
    """Source-asset kind: original / reference / processed product image."""

    ORIGINAL_IMAGE = "original_image"
    REFERENCE_IMAGE = "reference_image"
    PROCESSED_PRODUCT_IMAGE = "processed_product_image"


class ImageSessionAssetKind(StrEnum):
    """Image session attachment: user-uploaded reference / AI-generated image."""

    REFERENCE_UPLOAD = "reference_upload"
    GENERATED_IMAGE = "generated_image"


class JobStatus(StrEnum):
    """Continuous-generation task status: queued -> running -> succeeded/failed/cancelled."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CopyStatus(StrEnum):
    """Copy status: draft (editable) / confirmed (locked for poster)."""

    DRAFT = "draft"
    CONFIRMED = "confirmed"


class PosterKind(StrEnum):
    """Poster kind: main image / promotional poster."""

    MAIN_IMAGE = "main_image"
    PROMO_POSTER = "promo_poster"


class ProductWorkflowState(StrEnum):
    """Product workflow derived status: assets / copy / poster / failed."""

    DRAFT = "draft"
    COPY_READY = "copy_ready"
    POSTER_READY = "poster_ready"
    FAILED = "failed"


class WorkflowNodeType(StrEnum):
    """Product workflow node type."""

    PRODUCT_CONTEXT = "product_context"
    REFERENCE_IMAGE = "reference_image"
    COPY_GENERATION = "copy_generation"
    IMAGE_GENERATION = "image_generation"


class WorkflowNodeStatus(StrEnum):
    """ Internal documentation removed."""

    IDLE = "idle"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class WorkflowRunStatus(StrEnum):
    """ Internal documentation removed."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
