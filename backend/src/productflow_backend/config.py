from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

ConfigInputType = Literal["text", "password", "number", "boolean", "select", "multi_select", "textarea"]
IMAGE_SIZE_PATTERN = re.compile(r"^\d+x\d+$")
DEFAULT_IMAGE_GENERATION_MAX_DIMENSION = 3840
IMAGE_GENERATION_MIN_DIMENSION = 512
IMAGE_GENERATION_DIMENSION_MULTIPLE = 16
IMAGE_GENERATION_MIN_MAX_DIMENSION = 512
IMAGE_GENERATION_MAX_MAX_DIMENSION = 8192
IMAGE_GENERATION_MAX_DIMENSION = DEFAULT_IMAGE_GENERATION_MAX_DIMENSION
IMAGE_GENERATION_MAX_PIXELS = DEFAULT_IMAGE_GENERATION_MAX_DIMENSION * DEFAULT_IMAGE_GENERATION_MAX_DIMENSION
DEFAULT_IMAGE_SESSION_IDLE_TIMEOUT_MINUTES = 90
IMAGE_SESSION_IDLE_TIMEOUT_MIN_MINUTES = 1
IMAGE_SESSION_IDLE_TIMEOUT_MAX_MINUTES = 24 * 60
DEFAULT_IMAGE_SESSION_WORKER_FAILSAFE_TIME_LIMIT_MINUTES = 24 * 60
DEFAULT_WORKFLOW_IMAGE_GENERATION_PROVIDER_TIMEOUT_SECONDS = 15 * 60
IMAGE_SIZE_CONFIG_KEYS = {"image_main_image_size", "image_promo_poster_size"}
PROMPT_CONFIG_KEYS = {
    "prompt_brief_system",
    "prompt_copy_system",
    "prompt_poster_image_template",
    "prompt_poster_image_edit_template",
    "prompt_poster_image_reference_policy",
    "prompt_image_chat_template",
}
IMAGE_TOOL_FIELD_KEYS: tuple[str, ...] = (
    "model",
    "quality",
    "output_format",
    "output_compression",
    "background",
    "moderation",
    "action",
    "input_fidelity",
    "partial_images",
)
IMAGE_TOOL_LEGACY_FIELD_KEYS: tuple[str, ...] = ("n",)
DEFAULT_IMAGE_TOOL_ALLOWED_FIELDS: tuple[str, ...] = tuple(key for key in IMAGE_TOOL_FIELD_KEYS if key != "background")
DEFAULT_IMAGE_TOOL_ALLOWED_FIELDS_TEXT = ",".join(DEFAULT_IMAGE_TOOL_ALLOWED_FIELDS)
BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_LOG_DIR = BACKEND_DIR / "storage" / "logs"
DEFAULT_PROMPT_BRIEF_SYSTEM = (
    "You are an e-commerce product understanding assistant. Based on the product name, category, price, and purpose, "
    "produce concise structured JSON output. Do not output markdown."
)
DEFAULT_PROMPT_COPY_SYSTEM = (
    "You are a Taobao e-commerce copy assistant. Output JSON only, do not output markdown, "
    "and keep the language conversational and direct, suitable for main images and promotional posters."
)
DEFAULT_PROMPT_POSTER_IMAGE_TEMPLATE = """Generate an image based on this turn's user request and the explicitly connected upstream context.
User request: {instruction}
Output size: {size}
Upstream context: 
{context_block}
Visual reference rules: 
{reference_policy}
{kind_requirements}
Generate the image directly; do not return explanatory text."""
DEFAULT_PROMPT_POSTER_IMAGE_EDIT_TEMPLATE = DEFAULT_PROMPT_POSTER_IMAGE_TEMPLATE
DEFAULT_PROMPT_POSTER_IMAGE_REFERENCE_POLICY = (
    "If input images are provided, use the product/subject in the input images as the visual baseline; when product text information is weak, prioritise the subject in the image. "
    "Do not substitute unrelated characters, IPs, brands, products, or advertising themes. Copy is only used as auxiliary for selling points and layout."
)
DEFAULT_PROMPT_IMAGE_CHAT_TEMPLATE = """Generate an image based on this turn's user request.
Output size: {size}
{history_block}
This turn's user request: {prompt}
Generate the image directly; do not return explanatory text."""


@dataclass(frozen=True, slots=True)
class ConfigOption:
    value: str
    label: str


@dataclass(frozen=True, slots=True)
class ConfigDefinition:
    key: str
    label: str
    category: str
    input_type: ConfigInputType
    description: str = ""
    options: tuple[ConfigOption, ...] = ()
    secret: bool = False
    minimum: int | None = None
    maximum: int | None = None
    optional: bool = False


class Settings(BaseSettings):
    """Application configuration: env variables + database overrides.

    Infrastructure configuration (database / Redis / secrets) is read from env variables only, 
    while business configuration can be overridden at runtime via the app_settings table. Legacy text/image provider fields are only used as migration input.
    """

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 29280
    backend_cors_origins: str = "http://localhost:29281,http://127.0.0.1:29281"
    session_cookie_secure: bool = False

    admin_access_key: str = Field(min_length=8)
    settings_access_token: str | None = None
    session_secret: str = Field(min_length=16)

    database_url: str
    redis_url: str
    storage_root: Path = Path("./backend/storage")

    log_dir: Path = DEFAULT_LOG_DIR
    log_level: str = "INFO"
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 5
    log_retention_days: int = 14

    text_provider_kind: str = "mock"
    text_api_key: str | None = None
    text_base_url: str | None = None
    text_brief_model: str = "gpt-4o"
    text_copy_model: str = "gpt-4o"

    image_provider_kind: str = "mock"
    image_api_key: str | None = None
    image_base_url: str | None = None
    image_generate_model: str = "gpt-5.4"
    image_images_quality: str | None = None
    image_images_style: str | None = None
    image_responses_background_enabled: bool = True
    image_tool_model: str | None = None
    image_tool_quality: str | None = None
    image_tool_output_format: str | None = None
    image_tool_output_compression: int | None = Field(default=None, ge=0, le=100)
    image_tool_background: str | None = None
    image_tool_moderation: str | None = None
    image_tool_action: str | None = None
    image_tool_input_fidelity: str | None = None
    image_tool_partial_images: int | None = Field(default=None, ge=0, le=3)
    image_tool_n: int | None = Field(default=None, ge=1, le=10)
    image_tool_allowed_fields: str = DEFAULT_IMAGE_TOOL_ALLOWED_FIELDS_TEXT
    image_generation_max_dimension: int = Field(
        default=DEFAULT_IMAGE_GENERATION_MAX_DIMENSION,
        ge=IMAGE_GENERATION_MIN_MAX_DIMENSION,
        le=IMAGE_GENERATION_MAX_MAX_DIMENSION,
    )
    image_main_image_size: str = "1024x1024"
    image_promo_poster_size: str = "1024x1536"
    poster_generation_mode: str = "template"

    poster_font_path: Path = Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc")

    prompt_brief_system: str = DEFAULT_PROMPT_BRIEF_SYSTEM
    prompt_copy_system: str = DEFAULT_PROMPT_COPY_SYSTEM
    prompt_poster_image_template: str = DEFAULT_PROMPT_POSTER_IMAGE_TEMPLATE
    prompt_poster_image_edit_template: str = DEFAULT_PROMPT_POSTER_IMAGE_EDIT_TEMPLATE
    prompt_poster_image_reference_policy: str = DEFAULT_PROMPT_POSTER_IMAGE_REFERENCE_POLICY
    prompt_image_chat_template: str = DEFAULT_PROMPT_IMAGE_CHAT_TEMPLATE

    upload_max_image_bytes: int = 10 * 1024 * 1024
    upload_max_reference_images: int = 6
    upload_max_pixels: int = 16_000_000
    upload_allowed_image_mime_types: str = "image/png,image/jpeg,image/webp"

    generation_max_concurrent_tasks: int = Field(default=3, ge=1, le=20)
    image_session_stale_running_after_minutes: int = Field(
        default=DEFAULT_IMAGE_SESSION_IDLE_TIMEOUT_MINUTES,
        ge=IMAGE_SESSION_IDLE_TIMEOUT_MIN_MINUTES,
        le=IMAGE_SESSION_IDLE_TIMEOUT_MAX_MINUTES,
    )
    image_session_worker_failsafe_time_limit_minutes: int = Field(
        default=DEFAULT_IMAGE_SESSION_WORKER_FAILSAFE_TIME_LIMIT_MINUTES,
        ge=IMAGE_SESSION_IDLE_TIMEOUT_MIN_MINUTES,
        le=IMAGE_SESSION_IDLE_TIMEOUT_MAX_MINUTES,
    )
    workflow_image_generation_provider_timeout_seconds: int = Field(
        default=DEFAULT_WORKFLOW_IMAGE_GENERATION_PROVIDER_TIMEOUT_SECONDS,
        ge=1,
        le=24 * 60 * 60,
    )
    admin_access_required: bool = True
    deletion_enabled: bool = False

    @field_validator("image_main_image_size", "image_promo_poster_size")
    @classmethod
    def _normalize_image_generation_fallback_size(cls, value: str, info: ValidationInfo) -> str:
        max_dimension = int(info.data.get("image_generation_max_dimension") or DEFAULT_IMAGE_GENERATION_MAX_DIMENSION)
        return normalize_image_generation_size(value, max_dimension=max_dimension)

    @field_validator(
        "image_tool_model",
        "image_tool_quality",
        "image_tool_output_format",
        "image_tool_background",
        "image_tool_moderation",
        "image_tool_action",
        "image_tool_input_fidelity",
        "image_images_quality",
        "image_images_style",
        mode="before",
    )
    @classmethod
    def _normalize_optional_image_tool_text(cls, value: Any) -> str | None:
        normalized = "" if value is None else str(value).strip()
        return normalized or None

    @field_validator("image_tool_output_compression", "image_tool_partial_images", "image_tool_n", mode="before")
    @classmethod
    def _normalize_optional_image_tool_int(cls, value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return int(value)

    @field_validator("image_tool_allowed_fields", mode="before")
    @classmethod
    def _normalize_image_tool_allowed_fields(cls, value: Any) -> str:
        return normalize_image_tool_allowed_fields(value)

    @model_validator(mode="after")
    def _validate_distinct_settings_token(self) -> Settings:
        if self.settings_access_token and self.settings_access_token.strip() == self.admin_access_key:
            raise ValueError("SETTINGS_ACCESS_TOKEN must be configured separately from ADMIN_ACCESS_KEY")
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]

    @property
    def allowed_image_mime_types(self) -> set[str]:
        return {
            mime_type.strip().lower()
            for mime_type in self.upload_allowed_image_mime_types.split(",")
            if mime_type.strip()
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Bootstrap settings loaded from env.

    Infrastructure settings such as database URL, Redis URL, session secret and
    admin key intentionally stay env-backed because the app needs them before it
    can read any database-stored configuration.
    """

    return Settings()


CONFIG_DEFINITIONS: tuple[ConfigDefinition, ...] = (
    ConfigDefinition(
        key="image_tool_allowed_fields",
        label="Allowed tool fields",
        category="Image tool parameters",
        input_type="multi_select",
        options=tuple(ConfigOption(key, key) for key in IMAGE_TOOL_FIELD_KEYS),
        description=(
            "Controls advanced fields that the frontend may display and the backend may persist and send to the Responses image_generation tool; "
            "Images API n is derived automatically from candidate count or downstream node count, so it is not exposed as an optional field."
        ),
    ),
    ConfigDefinition(
        key="image_tool_model",
        label="Tool model",
        category="Image tool parameters",
        input_type="text",
        description="Leave blank to omit; requires provider support.",
        optional=True,
    ),
    ConfigDefinition(
        key="image_tool_quality",
        label="Quality",
        category="Image tool parameters",
        input_type="select",
        options=(
            ConfigOption("", "Default"),
            ConfigOption("auto", "Auto"),
            ConfigOption("low", "Low"),
            ConfigOption("medium", "Medium"),
            ConfigOption("high", "High"),
        ),
        optional=True,
    ),
    ConfigDefinition(
        key="image_tool_output_format",
        label="Format",
        category="Image tool parameters",
        input_type="select",
        options=(
            ConfigOption("", "Default"),
            ConfigOption("png", "PNG"),
            ConfigOption("jpeg", "JPEG"),
            ConfigOption("webp", "WebP"),
        ),
        optional=True,
    ),
    ConfigDefinition(
        key="image_tool_output_compression",
        label="Compression",
        category="Image tool parameters",
        input_type="number",
        description="0-100; leave blank to omit.",
        minimum=0,
        maximum=100,
        optional=True,
    ),
    ConfigDefinition(
        key="image_tool_background",
        label="returnedground",
        category="Image tool parameters",
        input_type="select",
        options=(
            ConfigOption("", "Default"),
            ConfigOption("auto", "Auto"),
            ConfigOption("opaque", "Opaque"),
            ConfigOption("transparent", "Transparent"),
        ),
        description="Sent only when 'background' is enabled under the allowed tool fields.",
        optional=True,
    ),
    ConfigDefinition(
        key="image_tool_moderation",
        label="Moderation",
        category="Image tool parameters",
        input_type="select",
        options=(ConfigOption("", "Default"), ConfigOption("auto", "Auto"), ConfigOption("low", "Low")),
        optional=True,
    ),
    ConfigDefinition(
        key="image_tool_action",
        label="Action",
        category="Image tool parameters",
        input_type="select",
        options=(
            ConfigOption("", "Default"),
            ConfigOption("auto", "Auto"),
            ConfigOption("generate", "Generate"),
            ConfigOption("edit", "edit"),
        ),
        optional=True,
    ),
    ConfigDefinition(
        key="image_tool_input_fidelity",
        label="Input fidelity",
        category="Image tool parameters",
        input_type="select",
        options=(ConfigOption("", "Default"), ConfigOption("low", "Low"), ConfigOption("high", "High")),
        optional=True,
    ),
    ConfigDefinition(
        key="image_tool_partial_images",
        label="Partial",
        category="Image tool parameters",
        input_type="number",
        description="0-3; leave blank to omit.",
        minimum=0,
        maximum=3,
        optional=True,
    ),
    ConfigDefinition(
        key="image_generation_max_dimension",
        label="Image generation max edge",
        category="Image generation",
        input_type="number",
        description="Max width/height in pixels for image-chat and workflow image generation; the max area is derived from the square of this value.",
        minimum=IMAGE_GENERATION_MIN_MAX_DIMENSION,
        maximum=IMAGE_GENERATION_MAX_MAX_DIMENSION,
    ),
    ConfigDefinition(
        key="image_main_image_size",
        label="Main image size (compat default)",
        category="Image generation",
        input_type="text",
        description=(
            "Advanced/compat defaults: only used when the image provider input does not explicitly include image_size, "
            "and when the generation kind is MAIN_IMAGE. New workflow image-generation nodes typically pass an explicit size, "
            "Prefer the size picker on each node."
        ),
    ),
    ConfigDefinition(
        key="image_promo_poster_size",
        label="Promotional poster size (compat default)",
        category="Image generation",
        input_type="text",
        description=(
            "Advanced/compat defaults: only used when the image provider input does not explicitly include image_size, "
            "and when the generation kind is PROMO_POSTER. New workflow image-generation nodes typically pass an explicit size, "
            "Prefer the size picker on each node."
        ),
    ),
    ConfigDefinition(
        key="poster_generation_mode",
        label="Poster generation mode",
        category="Poster and uploads",
        input_type="select",
        options=(ConfigOption("template", "Template render"), ConfigOption("generated", "AI generated")),
        description="The local template is used as a mock/dev fallback; when a real image provider is bound, workflow image generation automatically uses AI generation.",
    ),
    ConfigDefinition(
        key="poster_font_path",
        label="Poster font path",
        category="Poster and uploads",
        input_type="text",
        description="Font file used to render text in template posters and mock images.",
    ),
    ConfigDefinition(
        key="prompt_brief_system",
        label="Product understanding system prompt",
        category="Prompt",
        input_type="textarea",
        description="Used to understand product info; requires the model to output CreativeBrief JSON.",
    ),
    ConfigDefinition(
        key="prompt_copy_system",
        label="Copy generation system prompt",
        category="Prompt",
        input_type="textarea",
        description="Used for main-image/poster copy generation; requires the model to output Copy JSON.",
    ),
    ConfigDefinition(
        key="prompt_poster_image_template",
        label="Poster image-generation prompt template",
        category="Prompt",
        input_type="textarea",
        description=(
            "Used for workbench AI image generation. Available placeholders: instruction, size, context_block, reference_policy, "
            "kind, kind_label, kind_requirements. "
        ),
    ),
    ConfigDefinition(
        key="prompt_poster_image_edit_template",
        label="Image editing prompt template",
        category="Prompt",
        input_type="textarea",
        description=(
            "Used for continued generation from workbench reference/generated images. Available placeholders: instruction, size, context_block, "
            "reference_policy, kind, kind_label, kind_requirements. "
        ),
    ),
    ConfigDefinition(
        key="prompt_poster_image_reference_policy",
        label="Workbench visual reference policy",
        category="Prompt",
        input_type="textarea",
        description="Used as the reference_policy placeholder of the workbench image-generation template; the subject-priority rules can be adjusted in settings.",
    ),
    ConfigDefinition(
        key="prompt_image_chat_template",
        label="Image-chat prompt template",
        category="Prompt",
        input_type="textarea",
        description="Used for image-chat conversations. Available placeholders: prompt, size, history_block.",
    ),
    ConfigDefinition(
        key="upload_max_image_bytes",
        label="Max bytes per image",
        category="Poster and uploads",
        input_type="number",
        minimum=1,
    ),
    ConfigDefinition(
        key="upload_max_reference_images",
        label="Max reference image count",
        category="Poster and uploads",
        input_type="number",
        minimum=0,
    ),
    ConfigDefinition(
        key="upload_max_pixels",
        label="Max pixel count",
        category="Poster and uploads",
        input_type="number",
        minimum=1,
    ),
    ConfigDefinition(
        key="upload_allowed_image_mime_types",
        label="Allowed image MIME types",
        category="Poster and uploads",
        input_type="textarea",
        description="Comma-separated, e.g. image/png,image/jpeg,image/webp.",
    ),
    ConfigDefinition(
        key="generation_max_concurrent_tasks",
        label="Global generation concurrency limit",
        category="Generation queue",
        input_type="number",
        description="Global resource-protection threshold; workflow and image-chat retries are suggested once the limit is reached.",
        minimum=1,
        maximum=20,
    ),
    ConfigDefinition(
        key="image_session_stale_running_after_minutes",
        label="Image-chat progress idle-recovery threshold (minutes)",
        category="Generation queue",
        input_type="number",
        description=(
            "On worker startup recovery, running image-chat tasks are considered idle based on the most recent progress heartbeat; "
            "tasks without progress fall back to started_at."
        ),
        minimum=IMAGE_SESSION_IDLE_TIMEOUT_MIN_MINUTES,
        maximum=IMAGE_SESSION_IDLE_TIMEOUT_MAX_MINUTES,
    ),
    ConfigDefinition(
        key="workflow_image_generation_provider_timeout_seconds",
        label="Workflow image-generation provider timeout (seconds)",
        category="Generation queue",
        input_type="number",
        description="Project-level timeout cap for a single provider call from a workflow AI image-generation node; on timeout the call fails safely and releases generation-queue capacity.",
        minimum=1,
        maximum=24 * 60 * 60,
    ),
    ConfigDefinition(
        key="admin_access_required",
        label="Require admin access key",
        category="Security and operations",
        input_type="boolean",
        description=(
            "Enabled by default; the standard workbench and private API require ADMIN_ACCESS_KEY login. When disabled, SETTINGS_ACCESS_TOKEN is still required "
            "to view or modify system configuration."
        ),
    ),
    ConfigDefinition(
        key="deletion_enabled",
        label="Enable business deletion",
        category="Security and operations",
        input_type="boolean",
        description="Disabled by default; used by demo deployments to block deletion of products and image-chat sessions and preserve audit evidence.",
    ),
)

CONFIG_DEFINITION_BY_KEY: dict[str, ConfigDefinition] = {
    definition.key: definition for definition in CONFIG_DEFINITIONS
}
RUNTIME_CONFIG_KEYS: set[str] = set(CONFIG_DEFINITION_BY_KEY)


def normalize_image_size(value: Any, *, label: str = "Image size") -> str:
    """Validate and normalize image-size format as widthxheight."""
    normalized = "" if value is None else str(value).strip().lower()
    if not IMAGE_SIZE_PATTERN.fullmatch(normalized):
        raise ValueError(f"{label} must use widthxheight format, e.g. 1024x1024")
    width, height = (int(part) for part in normalized.split("x", maxsplit=1))
    if width <= 0 or height <= 0:
        raise ValueError(f"{label} width and height must be greater than 0")
    return normalized


def _runtime_image_generation_max_dimension() -> int:
    return int(get_runtime_settings().image_generation_max_dimension)


def _image_generation_max_dimension_multiple(max_dimension: int) -> int:
    return max_dimension - (max_dimension % IMAGE_GENERATION_DIMENSION_MULTIPLE)


def _nearest_image_generation_dimension_multiple(value: int, *, max_dimension: int) -> int:
    lower = (value // IMAGE_GENERATION_DIMENSION_MULTIPLE) * IMAGE_GENERATION_DIMENSION_MULTIPLE
    upper = lower + IMAGE_GENERATION_DIMENSION_MULTIPLE
    candidates = [
        candidate
        for candidate in {lower, upper}
        if IMAGE_GENERATION_MIN_DIMENSION <= candidate <= max_dimension
    ]
    if candidates:
        return min(candidates, key=lambda candidate: (abs(candidate - value), candidate))
    if value < IMAGE_GENERATION_MIN_DIMENSION:
        return IMAGE_GENERATION_MIN_DIMENSION
    return max_dimension


def normalize_image_generation_size(
    value: Any,
    *,
    label: str = "Image size",
    max_dimension: int | None = None,
) -> str:
    """Validate and clamp image-generation size, including format, positivity, and runtime safety bounds."""
    normalized = normalize_image_size(value, label=label)
    resolved_max_dimension = int(max_dimension or _runtime_image_generation_max_dimension())
    if (
        resolved_max_dimension < IMAGE_GENERATION_MIN_MAX_DIMENSION
        or resolved_max_dimension > IMAGE_GENERATION_MAX_MAX_DIMENSION
    ):
        raise ValueError(
            f"Image generation max edge must be in {IMAGE_GENERATION_MIN_MAX_DIMENSION}-{IMAGE_GENERATION_MAX_MAX_DIMENSION} "
        )
    effective_max_dimension = _image_generation_max_dimension_multiple(resolved_max_dimension)
    max_pixels = effective_max_dimension * effective_max_dimension
    width, height = (int(part) for part in normalized.split("x", maxsplit=1))
    scale = min(1.0, effective_max_dimension / width, effective_max_dimension / height)
    resolved_width = min(effective_max_dimension, max(IMAGE_GENERATION_MIN_DIMENSION, round(width * scale)))
    resolved_height = min(effective_max_dimension, max(IMAGE_GENERATION_MIN_DIMENSION, round(height * scale)))
    if resolved_width * resolved_height > max_pixels:
        pixel_scale = (max_pixels / (resolved_width * resolved_height)) ** 0.5
        resolved_width = max(1, int(resolved_width * pixel_scale))
        resolved_height = max(1, int(resolved_height * pixel_scale))
    resolved_width = _nearest_image_generation_dimension_multiple(resolved_width, max_dimension=effective_max_dimension)
    resolved_height = _nearest_image_generation_dimension_multiple(
        resolved_height,
        max_dimension=effective_max_dimension,
    )
    return f"{resolved_width}x{resolved_height}"


def parse_image_tool_allowed_fields(value: Any) -> tuple[str, ...]:
    if value is None:
        parts: list[str] = []
    elif isinstance(value, str):
        parts = [part.strip() for part in re.split(r"[\s,]+", value) if part.strip()]
    elif isinstance(value, list | tuple | set):
        parts = [str(part).strip() for part in value if str(part).strip()]
    else:
        parts = [str(value).strip()] if str(value).strip() else []

    selected = set(parts)
    unknown = selected - set(IMAGE_TOOL_FIELD_KEYS) - set(IMAGE_TOOL_LEGACY_FIELD_KEYS)
    if unknown:
        raise ValueError(f"Allowed tool fields contain unsupported fields: {', '.join(sorted(unknown))}")
    return tuple(key for key in IMAGE_TOOL_FIELD_KEYS if key in selected)


def normalize_image_tool_allowed_fields(value: Any) -> str:
    return ",".join(parse_image_tool_allowed_fields(value))


def filter_image_tool_options(
    tool_options: Mapping[str, Any] | None,
    *,
    allowed_fields: tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    if not tool_options:
        return None
    resolved_allowed_fields = (
        allowed_fields
        if allowed_fields is not None
        else parse_image_tool_allowed_fields(get_runtime_settings().image_tool_allowed_fields)
    )
    selected_fields = set(resolved_allowed_fields)
    normalized = {
        str(key): value
        for key, value in tool_options.items()
        if str(key) in selected_fields
        and value is not None
        and not (isinstance(value, str) and not value.strip())
    }
    return normalized or None


def normalize_config_value(key: str, value: Any) -> str:
    definition = CONFIG_DEFINITION_BY_KEY.get(key)
    if definition is None:
        raise ValueError(f"Unknown configuration key: {key}")

    if definition.input_type == "boolean":
        if isinstance(value, bool):
            return "true" if value else "false"
        normalized_bool = str(value).strip().lower()
        if normalized_bool in {"1", "true", "yes", "on"}:
            return "true"
        if normalized_bool in {"0", "false", "no", "off"}:
            return "false"
        raise ValueError(f"{definition.label} must be a boolean")

    if definition.input_type == "multi_select":
        return normalize_image_tool_allowed_fields(value)

    if definition.input_type == "number":
        if definition.optional and (value is None or str(value).strip() == ""):
            return ""
        try:
            normalized_int = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{definition.label} must be an integer") from exc
        if definition.minimum is not None and normalized_int < definition.minimum:
            raise ValueError(f"{definition.label} must not be less than {definition.minimum}")
        if definition.maximum is not None and normalized_int > definition.maximum:
            raise ValueError(f"{definition.label} must not be greater than {definition.maximum}")
        return str(normalized_int)

    if key in IMAGE_SIZE_CONFIG_KEYS:
        return normalize_image_generation_size(value, label=definition.label)
    normalized = "" if value is None else str(value).strip()
    if key in PROMPT_CONFIG_KEYS and not normalized:
        raise ValueError(f"{definition.label} must not be empty; restore default to revert")
    if definition.input_type == "select":
        allowed_values = {option.value for option in definition.options}
        if normalized not in allowed_values:
            allowed_text = ", ".join(sorted(allowed_values))
            raise ValueError(f"{definition.label} must be one of: {allowed_text}")
    return normalized


def normalize_config_values(values: Mapping[str, Any]) -> dict[str, str]:
    return {key: normalize_config_value(key, value) for key, value in values.items()}


def build_settings_with_overrides(overrides: Mapping[str, str]) -> Settings:
    try:
        return Settings(**dict(overrides))
    except ValidationError as exc:
        first_error = exc.errors()[0] if exc.errors() else {}
        field = ".".join(str(part) for part in first_error.get("loc", []))
        message = first_error.get("msg") or str(exc)
        raise ValueError(f"Configuration validation failed {field}: {message}") from exc


def _load_database_config_overrides() -> dict[str, str]:
    try:
        from productflow_backend.infrastructure.db.models import AppSetting
        from productflow_backend.infrastructure.db.session import get_session_factory

        session = get_session_factory()()
        try:
            rows = session.scalars(select(AppSetting).where(AppSetting.key.in_(RUNTIME_CONFIG_KEYS))).all()
            return {row.key: row.value for row in rows}
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001
        if exc.__class__.__name__ in {"OperationalError", "ProgrammingError"}:
            return {}
        if isinstance(exc, SQLAlchemyError):
            return {}
        raise


def get_runtime_settings() -> Settings:
    """Settings with database overrides applied.

    If a key does not exist in the database, env/default Settings remains the
    fallback. Missing app_settings table is tolerated so fresh databases can
    still start before migrations have run.
    """

    overrides = _load_database_config_overrides()
    if not overrides:
        return get_settings()
    return build_settings_with_overrides(overrides)
