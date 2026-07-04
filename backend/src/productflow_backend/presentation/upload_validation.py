from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from fastapi import HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError

from productflow_backend.config import get_runtime_settings


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    content: bytes
    filename: str
    mime_type: str


_IMAGE_FORMAT_MIME_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
}


async def read_validated_image_upload(upload: UploadFile, *, fallback_filename: str) -> ValidatedUpload:
    """Image MIME  /  /  / Format """
    settings = get_runtime_settings()
    filename = upload.filename or fallback_filename
    declared_mime = (upload.content_type or "application/octet-stream").split(";", maxsplit=1)[0].strip().lower()
    if declared_mime not in settings.allowed_image_mime_types:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported image type: {declared_mime}",
        )

    content = await upload.read(settings.upload_max_image_bytes + 1)
    if len(content) > settings.upload_max_image_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Image exceeds the size limit: {settings.upload_max_image_bytes} bytes",
        )
    if not content:
        raise HTTPException(status_code=400, detail="Image content must not be empty")

    try:
        with Image.open(BytesIO(content)) as image:
            image.verify()
        with Image.open(BytesIO(content)) as image:
            width, height = image.size
            detected_mime = _IMAGE_FORMAT_MIME_TYPES.get(image.format or "")
    except (OSError, UnidentifiedImageError) as exc:
        raise HTTPException(status_code=400, detail="Uploaded file is not a decodable image") from exc

    if width <= 0 or height <= 0 or width * height > settings.upload_max_pixels:
        raise HTTPException(
            status_code=400,
            detail=f"Image pixel count exceeds the limit: {settings.upload_max_pixels}",
        )
    if detected_mime not in settings.allowed_image_mime_types:
        raise HTTPException(status_code=415, detail="Unsupported real image format")
    if detected_mime != declared_mime:
        raise HTTPException(status_code=400, detail="Image content format does not match Content-Type")

    return ValidatedUpload(content=content, filename=filename, mime_type=detected_mime)


def validate_reference_image_count(count: int) -> None:
    max_count = get_runtime_settings().upload_max_reference_images
    if count > max_count:
        raise HTTPException(status_code=400, detail=f"Maximum reference images allowed: {max_count}")
