from __future__ import annotations

from base64 import b64encode
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from dramatiq.middleware.time_limit import TimeLimitExceeded
from fastapi.testclient import TestClient
from helpers import (
    _enable_deletion,
    _execute_workflow_queue_inline,
    _login,
    _make_demo_image_bytes,
    _make_demo_image_bytes_with_size,
    _read_image_size,
)

from productflow_backend.config import get_settings
from productflow_backend.infrastructure.db.models import (
    AppSetting,
    ImageSession,
    ImageSessionAsset,
    ImageSessionGenerationTask,
    ImageSessionRound,
    ProviderBinding,
    ProviderProfile,
)


@pytest.fixture(autouse=True)
def _execute_workflow_queue_inline_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep API workflow tests deterministic while production delivery goes through Dramatiq."""

    _execute_workflow_queue_inline(monkeypatch)
    from productflow_backend.application.image_sessions import execute_image_session_generation_task

    monkeypatch.setattr(
        "productflow_backend.application.image_sessions.enqueue_image_session_generation_task",
        execute_image_session_generation_task,
    )


def test_image_session_rounds_support_same_conversation(configured_env: Path) -> None:
    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)

    _login(client)

    created = client.post("/api/image-sessions", json={"title": "hand creamcontinuousimage generation"})
    assert created.status_code == 201
    session_id = created.json()["id"]

    first = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={
            "prompt": "make oneimagecreamy texturehand creamad image，soft light，white background，product centered ",
            "size": "1024x1024",
        },
    )
    assert first.status_code == 202
    first_payload = first.json()
    assert len(first_payload["rounds"]) == 1
    first_asset_id = first_payload["rounds"][0]["generated_asset"]["id"]
    assert first_payload["rounds"][0]["generated_asset"]["download_url"].startswith("/api/image-session-assets/")
    assert first_payload["rounds"][0]["generated_asset"]["preview_url"].endswith("variant=preview")
    assert first_payload["rounds"][0]["generated_asset"]["thumbnail_url"].endswith("variant=thumbnail")
    thumbnail = client.get(first_payload["rounds"][0]["generated_asset"]["thumbnail_url"])
    assert thumbnail.status_code == 200
    assert max(_read_image_size(thumbnail.content)) <= 320

    upload = client.post(
        f"/api/image-sessions/{session_id}/reference-images",
        files={"reference_images": ("sample.png", _make_demo_image_bytes(), "image/png")},
    )
    assert upload.status_code == 200
    upload_payload = upload.json()
    assert any(asset["kind"] == "reference_upload" for asset in upload_payload["assets"])

    missing_base = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={
            "prompt": "keep the same product and lighting，change the background to a bathroom counter，add a few water droplets",
            "size": "1024x1024",
        },
    )
    assert missing_base.status_code == 400
    assert missing_base.json()["detail"] == "Follow-up image generation must select a previously generated image from this session as the base image"

    second = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={
            "prompt": "keep the same product and lighting，change the background to a bathroom counter，add a few water droplets",
            "size": "1024x1024",
            "base_asset_id": first_asset_id,
        },
    )
    assert second.status_code == 202
    second_payload = second.json()
    assert len(second_payload["rounds"]) == 2
    assert second_payload["rounds"][-1]["provider_name"] == "mock"
    assert second_payload["rounds"][-1]["assistant_message"].startswith("selected for this roundimagecontext")
    assert second_payload["rounds"][-1]["previous_response_id"] is None
    assert second_payload["rounds"][-1]["base_asset_id"] == first_asset_id
    assert second_payload["rounds"][-1]["selected_reference_asset_ids"] == []


def test_image_session_generate_returns_queued_task_without_waiting_for_provider(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.presentation.api import create_app

    sent: list[str] = []
    monkeypatch.setattr(
        "productflow_backend.application.image_sessions.enqueue_image_session_generation_task",
        lambda task_id: sent.append(task_id),
    )
    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post("/api/image-sessions", json={"title": "async submit"})
    assert created.status_code == 201
    response = client.post(
        f"/api/image-sessions/{created.json()['id']}/generate",
        json={"prompt": "only create tasks，do not wait provider", "size": "1024x1024"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["rounds"] == []
    assert len(payload["generation_tasks"]) == 1
    task = payload["generation_tasks"][0]
    assert task["status"] == "queued"
    assert task["prompt"] == "only create tasks，do not wait provider"
    assert task["completed_candidates"] == 0
    assert task["active_candidate_index"] is None
    assert task["progress_phase"] is None
    assert task["progress_updated_at"] is None
    assert task["provider_response_id"] is None
    assert task["provider_response_status"] is None
    assert task["progress_metadata"] is None
    assert task["attempts"] == 0
    assert task["is_retryable"] is True
    assert task["is_cancelable"] is True
    assert task["queue_active_count"] == 1
    assert task["queue_running_count"] == 0
    assert task["queue_queued_count"] == 1
    assert task["queued_ahead_count"] == 0
    assert task["queue_position"] == 1
    assert sent == [task["id"]]
    db_session.expire_all()
    persisted = db_session.get(ImageSessionGenerationTask, task["id"])
    assert persisted is not None
    assert persisted.status == "queued"

    duplicate_without_base = client.post(
        f"/api/image-sessions/{created.json()['id']}/generate",
        json={"prompt": "firstimageCannot submit without a base image while still pending", "size": "1024x1024"},
    )
    assert duplicate_without_base.status_code == 400
    assert duplicate_without_base.json()["detail"] == "Follow-up image generation must select a previously generated image from this session as the base image"
    assert sent == [task["id"]]


def test_first_queued_image_session_task_without_base_still_executes_if_later_task_exists(
    configured_env: Path,
    db_session,
) -> None:
    from productflow_backend.application.image_sessions import (
        create_image_session,
        create_image_session_generation_task,
        execute_image_session_generation_task,
    )
    from productflow_backend.domain.enums import JobStatus

    image_session = create_image_session(db_session, product_id=None, title="first task worker validate")
    result = create_image_session_generation_task(
        db_session,
        image_session_id=image_session.id,
        prompt="first base image",
        size="1024x1024",
    )
    first_task_id = result.task.id

    db_session.add(
        ImageSessionGenerationTask(
            session_id=image_session.id,
            status=JobStatus.QUEUED,
            prompt="simulate a later historical task",
            size="1024x1024",
            generation_count=1,
        )
    )
    db_session.commit()

    execute_image_session_generation_task(first_task_id)

    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, first_task_id)
    rounds = db_session.query(ImageSessionRound).filter(ImageSessionRound.session_id == image_session.id).all()
    assert task is not None
    assert task.status == "succeeded"
    assert len(rounds) == 1
    assert rounds[0].base_asset_id is None


def test_image_session_status_returns_lightweight_task_snapshot(
    configured_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.application.image_sessions import execute_image_session_generation_task
    from productflow_backend.presentation.api import create_app

    sent: list[str] = []
    monkeypatch.setattr(
        "productflow_backend.application.image_sessions.enqueue_image_session_generation_task",
        lambda task_id: sent.append(task_id),
    )
    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post("/api/image-sessions", json={"title": "lightweight state"})
    assert created.status_code == 201
    session_id = created.json()["id"]
    submitted = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "poll status only", "size": "1024x1024"},
    )
    assert submitted.status_code == 202
    task_id = submitted.json()["generation_tasks"][0]["id"]

    queued_status = client.get(f"/api/image-sessions/{session_id}/status")
    assert queued_status.status_code == 200
    queued_payload = queued_status.json()
    assert "assets" not in queued_payload
    assert "rounds" not in queued_payload
    assert queued_payload["rounds_count"] == 0
    assert queued_payload["latest_round_id"] is None
    assert queued_payload["has_active_generation_task"] is True
    assert queued_payload["generation_tasks"][0]["id"] == task_id
    assert queued_payload["generation_tasks"][0]["status"] == "queued"
    assert queued_payload["generation_tasks"][0]["attempts"] == 0
    assert queued_payload["generation_tasks"][0]["is_retryable"] is True
    assert queued_payload["generation_tasks"][0]["is_cancelable"] is True
    assert queued_payload["generation_tasks"][0]["queue_position"] == 1
    assert sent == [task_id]

    execute_image_session_generation_task(task_id)

    completed_status = client.get(f"/api/image-sessions/{session_id}/status")
    assert completed_status.status_code == 200
    completed_payload = completed_status.json()
    assert completed_payload["rounds_count"] == 1
    assert completed_payload["latest_round_id"]
    assert completed_payload["latest_generation_group_id"]
    assert completed_payload["has_active_generation_task"] is False
    assert completed_payload["generation_tasks"][0]["status"] == "succeeded"
    assert completed_payload["generation_tasks"][0]["completed_candidates"] == 1
    assert completed_payload["generation_tasks"][0]["attempts"] == 1
    assert completed_payload["generation_tasks"][0]["is_retryable"] is False
    assert completed_payload["generation_tasks"][0]["is_cancelable"] is False
    assert completed_payload["generation_tasks"][0]["progress_phase"] == "succeeded"
    assert completed_payload["generation_tasks"][0]["progress_updated_at"] is not None
    assert completed_payload["generation_tasks"][0]["result_generation_group_id"] == completed_payload[
        "latest_generation_group_id"
    ]


def test_image_session_generation_accepts_per_request_tool_options_and_exposes_provider_notes(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.infrastructure.image.chat_service import GeneratedChatImage
    from productflow_backend.presentation.api import create_app

    calls: list[dict | None] = []

    def generate_with_note(self, **kwargs) -> GeneratedChatImage:
        calls.append(kwargs.get("tool_options"))
        return GeneratedChatImage(
            bytes_data=_make_demo_image_bytes_with_size(1024, 1024),
            mime_type="image/png",
            model_name="mock-image-chat-v1",
            provider_name="mock",
            prompt_version="test-v1",
            size=kwargs["size"],
            generated_at=datetime.now(UTC),
            provider_request_json={"tool_options": kwargs.get("tool_options")},
            provider_output_json={
                "_productflow": {
                    "notes": [{"kind": "fallback", "message": "provider does not support some parameters，completed using base parameters。"}]
                }
            },
        )

    monkeypatch.setattr(
        "productflow_backend.infrastructure.image.chat_service.ImageChatService.generate",
        generate_with_note,
    )
    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post("/api/image-sessions", json={"title": "per roundparameter"})
    assert created.status_code == 201
    response = client.post(
        f"/api/image-sessions/{created.json()['id']}/generate",
        json={
            "prompt": "per round override tool parameter",
            "size": "1024x1024",
            "tool_options": {
                "model": "gpt-image-2",
                "quality": "high",
                "output_format": "webp",
                "output_compression": 72,
                "background": "transparent",
                "moderation": "low",
                "action": "generate",
                "input_fidelity": "high",
                "partial_images": 1,
            },
        },
    )

    assert response.status_code == 202
    payload = response.json()
    expected_options = {
        "model": "gpt-image-2",
        "quality": "high",
        "output_format": "webp",
        "output_compression": 72,
        "moderation": "low",
        "action": "generate",
        "input_fidelity": "high",
        "partial_images": 1,
    }
    assert calls == [expected_options]
    assert payload["generation_tasks"][0]["tool_options"] == expected_options
    assert payload["generation_tasks"][0]["provider_notes"] == ["provider does not support some parameters，completed using base parameters。"]
    assert payload["rounds"][0]["provider_notes"] == ["provider does not support some parameters，completed using base parameters。"]
    assert payload["rounds"][0]["actual_size"] == "1024x1024"

    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, payload["generation_tasks"][0]["id"])
    assert task is not None
    assert task.tool_options == expected_options

    db_session.add(
        AppSetting(
            key="image_tool_allowed_fields",
            value="model,quality,output_format,output_compression,moderation,action,input_fidelity,partial_images,n",
        )
    )
    db_session.commit()

    explicit_session = client.post("/api/image-sessions", json={"title": "explicitly allowed n"})
    assert explicit_session.status_code == 201
    explicitly_allowed = client.post(
        f"/api/image-sessions/{explicit_session.json()['id']}/generate",
        json={
            "prompt": "explicitly allowed n",
            "size": "1024x1024",
            "tool_options": {"quality": "high", "n": 2},
        },
    )
    assert explicitly_allowed.status_code == 202
    assert calls[-1] == {"quality": "high"}
    assert explicitly_allowed.json()["generation_tasks"][-1]["tool_options"] == {"quality": "high"}

    invalid = client.post(
        f"/api/image-sessions/{created.json()['id']}/generate",
        json={
            "prompt": "invalid tool parameter",
            "size": "1024x1024",
            "tool_options": {"output_compression": 101},
        },
    )
    assert invalid.status_code == 422


def test_image_session_generation_exposes_actual_size_when_provider_downscales(
    configured_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.infrastructure.image.chat_service import GeneratedChatImage
    from productflow_backend.presentation.api import create_app

    def generate_downscaled(self, **kwargs) -> GeneratedChatImage:
        return GeneratedChatImage(
            bytes_data=_make_demo_image_bytes_with_size(1024, 1024),
            mime_type="image/png",
            model_name="mock-image-chat-v1",
            provider_name="mock",
            prompt_version="test-v1",
            size=kwargs["size"],
            generated_at=datetime.now(UTC),
            provider_request_json={"size": kwargs["size"]},
            provider_output_json={},
        )

    monkeypatch.setattr(
        "productflow_backend.infrastructure.image.chat_service.ImageChatService.generate",
        generate_downscaled,
    )
    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post("/api/image-sessions", json={"title": "size feedback"})
    assert created.status_code == 201
    response = client.post(
        f"/api/image-sessions/{created.json()['id']}/generate",
        json={"prompt": "request 2K butproviderreturn 1K", "size": "2048x2048"},
    )

    assert response.status_code == 202
    round_payload = response.json()["rounds"][0]
    assert round_payload["size"] == "2048x2048"
    assert round_payload["actual_size"] == "1024x1024"
    assert round_payload["provider_notes"] == ["Provider actually returned 1024x1024, request size is 2048x2048."]


def test_image_session_generate_enqueue_failure_marks_task_failed(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.presentation.api import create_app

    def fail_enqueue(task_id: str) -> None:
        raise RuntimeError(f"redis down for {task_id}")

    monkeypatch.setattr(
        "productflow_backend.application.image_sessions.enqueue_image_session_generation_task",
        fail_enqueue,
    )
    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post("/api/image-sessions", json={"title": "enqueuefailed"})
    assert created.status_code == 201
    response = client.post(
        f"/api/image-sessions/{created.json()['id']}/generate",
        json={"prompt": "enqueuefailedshould persist", "size": "1024x1024"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Task queue is unavailable; please retry later"
    db_session.expire_all()
    tasks = db_session.query(ImageSessionGenerationTask).all()
    assert len(tasks) == 1
    assert tasks[0].status == "failed"
    assert tasks[0].failure_reason == "Task queue is unavailable; please retry later"
    assert tasks[0].is_retryable is True


def test_image_session_manual_retry_resets_failed_task_and_enqueues(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.domain.enums import JobStatus
    from productflow_backend.presentation.api import create_app

    sent: list[str] = []
    monkeypatch.setattr(
        "productflow_backend.application.image_sessions.enqueue_image_session_generation_task",
        lambda task_id: sent.append(task_id),
    )
    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post("/api/image-sessions", json={"title": "manual retry"})
    assert created.status_code == 201
    session_id = created.json()["id"]
    submitted = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "firstfailedretry again", "size": "1024x1024"},
    )
    assert submitted.status_code == 202
    task_id = submitted.json()["generation_tasks"][0]["id"]

    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, task_id)
    assert task is not None
    task.status = JobStatus.FAILED
    task.failure_reason = "Image generation failed, please retry later"
    task.finished_at = datetime.now(UTC)
    task.attempts = 3
    task.is_retryable = True
    db_session.commit()

    sent.clear()
    retried = client.post(f"/api/image-sessions/{session_id}/generation-tasks/{task_id}/retry")

    assert retried.status_code == 202
    retry_payload = retried.json()["generation_tasks"][0]
    assert retry_payload["id"] == task_id
    assert retry_payload["status"] == "queued"
    assert retry_payload["failure_reason"] is None
    assert retry_payload["attempts"] == 3
    assert retry_payload["is_retryable"] is True
    assert sent == [task_id]
    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, task_id)
    assert task is not None
    assert task.status == JobStatus.QUEUED
    assert task.failure_reason is None
    assert task.finished_at is None
    assert task.is_retryable is True


def test_image_session_manual_cancel_marks_active_task_cancelled_and_worker_noops(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.application.image_sessions import execute_image_session_generation_task
    from productflow_backend.domain.enums import JobStatus
    from productflow_backend.presentation.api import create_app

    monkeypatch.setattr(
        "productflow_backend.application.image_sessions.enqueue_image_session_generation_task",
        lambda task_id: None,
    )
    monkeypatch.setattr(
        "productflow_backend.application.image_sessions._execute_image_session_round_generation",
        lambda *args, **kwargs: pytest.fail("cancelled image session task must no-op"),
    )
    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post("/api/image-sessions", json={"title": "cancel generation task"})
    assert created.status_code == 201
    session_id = created.json()["id"]
    submitted = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "first submit then cancel", "size": "1024x1024"},
    )
    assert submitted.status_code == 202
    task_id = submitted.json()["generation_tasks"][0]["id"]

    cancelled = client.post(f"/api/image-sessions/{session_id}/generation-tasks/{task_id}/cancel")
    assert cancelled.status_code == 200
    task_payload = cancelled.json()["generation_tasks"][0]
    assert task_payload["id"] == task_id
    assert task_payload["status"] == "cancelled"
    assert task_payload["failure_reason"] == "cancelled"
    assert task_payload["progress_phase"] == "cancelled"
    assert task_payload["is_retryable"] is False
    assert task_payload["is_cancelable"] is False

    execute_image_session_generation_task(task_id)
    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, task_id)
    assert task is not None
    assert task.status == JobStatus.CANCELLED
    assert task.finished_at is not None
    assert task.is_retryable is False


def test_image_session_generation_cancel_after_file_save_does_not_persist_round_or_asset(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.application.image_sessions import (
        IMAGE_SESSION_CANCELLED_REASON,
        ImageSessionGenerationCancelledError,
        _execute_image_session_round_generation,
        create_image_session,
        create_image_session_generation_task,
    )
    from productflow_backend.domain.enums import JobStatus
    from productflow_backend.infrastructure.image.chat_service import GeneratedChatImage
    from productflow_backend.infrastructure.storage import LocalStorage

    def generate_success(self, **kwargs) -> GeneratedChatImage:
        return GeneratedChatImage(
            bytes_data=_make_demo_image_bytes_with_size(1024, 1024),
            mime_type="image/png",
            model_name="mock-image-chat-v1",
            provider_name="mock",
            prompt_version="test-v1",
            size=kwargs["size"],
            generated_at=datetime.now(UTC),
            provider_request_json={"size": kwargs["size"]},
            provider_output_json={},
        )

    class CancellingStorage:
        def __init__(self) -> None:
            self.inner = LocalStorage()
            self.saved_relative_path: str | None = None

        def __getattr__(self, name: str):
            return getattr(self.inner, name)

        def save_image_session_generated(self, session_id: str, content: bytes, suffix: str = ".png") -> str:
            relative_path = self.inner.save_image_session_generated(session_id, content, suffix=suffix)
            self.saved_relative_path = relative_path
            task = db_session.get(ImageSessionGenerationTask, task_id)
            assert task is not None
            task.status = JobStatus.CANCELLED
            task.failure_reason = IMAGE_SESSION_CANCELLED_REASON
            task.finished_at = datetime.now(UTC)
            task.progress_phase = "cancelled"
            task.progress_updated_at = datetime.now(UTC)
            task.is_retryable = False
            db_session.commit()
            return relative_path

    monkeypatch.setattr(
        "productflow_backend.infrastructure.image.chat_service.ImageChatService.generate",
        generate_success,
    )

    image_session = create_image_session(db_session, product_id=None, title="cancel after save")
    result = create_image_session_generation_task(
        db_session,
        image_session_id=image_session.id,
        prompt="provider Returned but cancelled before or after saving",
        size="1024x1024",
    )
    task_id = result.task.id
    result.task.status = JobStatus.RUNNING
    result.task.started_at = datetime.now(UTC)
    db_session.commit()
    storage = CancellingStorage()

    with pytest.raises(ImageSessionGenerationCancelledError):
        _execute_image_session_round_generation(
            db_session,
            image_session_id=image_session.id,
            prompt=result.task.prompt,
            size=result.task.size,
            generation_task_id=task_id,
            storage=storage,
        )

    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, task_id)
    rounds = db_session.query(ImageSessionRound).filter(ImageSessionRound.session_id == image_session.id).all()
    assets = db_session.query(ImageSessionAsset).filter(ImageSessionAsset.session_id == image_session.id).all()

    assert task is not None
    assert task.status == JobStatus.CANCELLED
    assert task.failure_reason == IMAGE_SESSION_CANCELLED_REASON
    assert task.is_retryable is False
    assert task.completed_candidates == 0
    assert rounds == []
    assert assets == []
    assert storage.saved_relative_path is not None
    assert not Path(configured_env, storage.saved_relative_path).exists()


def test_image_session_generation_cancelled_task_is_not_overwritten_by_late_failure(
    configured_env: Path,
    db_session,
) -> None:
    from productflow_backend.application.image_sessions import (
        IMAGE_SESSION_CANCELLED_REASON,
        _handle_image_generation_task_failure_safely,
        create_image_session,
        create_image_session_generation_task,
    )
    from productflow_backend.domain.enums import JobStatus

    image_session = create_image_session(db_session, product_id=None, title="after cancelfailedno override")
    result = create_image_session_generation_task(
        db_session,
        image_session_id=image_session.id,
        prompt="after cancel provider only then error",
        size="1024x1024",
    )
    result.task.status = JobStatus.CANCELLED
    result.task.failure_reason = IMAGE_SESSION_CANCELLED_REASON
    result.task.finished_at = datetime.now(UTC)
    result.task.progress_phase = "cancelled"
    result.task.progress_updated_at = datetime.now(UTC)
    result.task.is_retryable = False
    result.task.attempts = 1
    db_session.commit()

    _handle_image_generation_task_failure_safely(
        db_session,
        task_id=result.task.id,
        reason="Image generation failed, please retry later",
    )

    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, result.task.id)
    assert task is not None
    assert task.status == JobStatus.CANCELLED
    assert task.failure_reason == IMAGE_SESSION_CANCELLED_REASON
    assert task.progress_phase == "cancelled"
    assert task.is_retryable is False


def test_image_session_manual_cancel_rejects_terminal_task(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.domain.enums import JobStatus
    from productflow_backend.presentation.api import create_app

    monkeypatch.setattr(
        "productflow_backend.application.image_sessions.enqueue_image_session_generation_task",
        lambda task_id: None,
    )
    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post("/api/image-sessions", json={"title": "cancel after end"})
    assert created.status_code == 201
    session_id = created.json()["id"]
    submitted = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "set task to success after creation", "size": "1024x1024"},
    )
    assert submitted.status_code == 202
    task_id = submitted.json()["generation_tasks"][0]["id"]

    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, task_id)
    assert task is not None
    task.status = JobStatus.SUCCEEDED
    task.finished_at = datetime.now(UTC)
    task.is_retryable = False
    db_session.commit()

    cancelled = client.post(f"/api/image-sessions/{session_id}/generation-tasks/{task_id}/cancel")

    assert cancelled.status_code == 400
    assert cancelled.json()["detail"] == "Already-finished generation tasks cannot be cancelled"


def test_image_session_manual_retry_rejects_non_failed_task(
    configured_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.presentation.api import create_app

    monkeypatch.setattr(
        "productflow_backend.application.image_sessions.enqueue_image_session_generation_task",
        lambda task_id: None,
    )
    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post("/api/image-sessions", json={"title": "invalid retry"})
    assert created.status_code == 201
    session_id = created.json()["id"]
    submitted = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "queued cannot retry", "size": "1024x1024"},
    )
    assert submitted.status_code == 202
    task_id = submitted.json()["generation_tasks"][0]["id"]

    retried = client.post(f"/api/image-sessions/{session_id}/generation-tasks/{task_id}/retry")

    assert retried.status_code == 400
    assert retried.json()["detail"] == "Only failed generation tasks can be retried"


def test_image_session_manual_retry_rejects_non_retryable_failed_task(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.domain.enums import JobStatus
    from productflow_backend.presentation.api import create_app

    monkeypatch.setattr(
        "productflow_backend.application.image_sessions.enqueue_image_session_generation_task",
        lambda task_id: None,
    )
    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post("/api/image-sessions", json={"title": "non-retryablefailedtask"})
    assert created.status_code == 201
    session_id = created.json()["id"]
    submitted = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "make non-retryable after creationfailed", "size": "1024x1024"},
    )
    assert submitted.status_code == 202
    task_id = submitted.json()["generation_tasks"][0]["id"]

    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, task_id)
    assert task is not None
    task.status = JobStatus.FAILED
    task.failure_reason = "oldfailedtasknon-retryable"
    task.finished_at = datetime.now(UTC)
    task.is_retryable = False
    db_session.commit()

    retried = client.post(f"/api/image-sessions/{session_id}/generation-tasks/{task_id}/retry")

    assert retried.status_code == 400
    assert retried.json()["detail"] == "This generation task cannot be retried"


def test_image_session_manual_retry_enqueue_failure_keeps_task_retryable(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.domain.enums import JobStatus
    from productflow_backend.presentation.api import create_app

    sent: list[str] = []
    monkeypatch.setattr(
        "productflow_backend.application.image_sessions.enqueue_image_session_generation_task",
        lambda task_id: sent.append(task_id),
    )
    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post("/api/image-sessions", json={"title": "retry enqueuefailed"})
    assert created.status_code == 201
    session_id = created.json()["id"]
    submitted = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "create task", "size": "1024x1024"},
    )
    assert submitted.status_code == 202
    task_id = submitted.json()["generation_tasks"][0]["id"]

    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, task_id)
    assert task is not None
    task.status = JobStatus.FAILED
    task.failure_reason = "Image generation failed, please retry later"
    task.finished_at = datetime.now(UTC)
    task.is_retryable = True
    db_session.commit()

    def fail_enqueue(task_id: str) -> None:
        raise RuntimeError(f"redis down for {task_id}")

    monkeypatch.setattr(
        "productflow_backend.application.image_sessions.enqueue_image_session_generation_task",
        fail_enqueue,
    )

    retried = client.post(f"/api/image-sessions/{session_id}/generation-tasks/{task_id}/retry")

    assert retried.status_code == 503
    assert retried.json()["detail"] == "Task queue is unavailable; please retry later"
    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, task_id)
    assert task is not None
    assert task.status == JobStatus.FAILED
    assert task.failure_reason == "Task queue is unavailable; please retry later"
    assert task.is_retryable is True


def test_image_session_worker_auto_retry_caps_and_uses_generic_safe_reason(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.application.image_sessions import IMAGE_SESSION_GENERATION_MAX_ATTEMPTS
    from productflow_backend.presentation.api import create_app

    calls = {"count": 0}

    def fail_generate(*args, **kwargs) -> None:
        calls["count"] += 1
        raise RuntimeError("provider raw secret sk-test path=/tmp/provider-traceback")

    monkeypatch.setattr(
        "productflow_backend.infrastructure.image.chat_service.ImageChatService.generate",
        fail_generate,
    )
    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post("/api/image-sessions", json={"title": "provider failed"})
    assert created.status_code == 201
    response = client.post(
        f"/api/image-sessions/{created.json()['id']}/generate",
        json={"prompt": "this time provider wouldfailed", "size": "1024x1024"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["generation_tasks"][0]["status"] == "failed"
    assert payload["generation_tasks"][0]["failure_reason"] == "Image generation failed, please retry later"
    assert "sk-test" not in payload["generation_tasks"][0]["failure_reason"]
    assert payload["generation_tasks"][0]["attempts"] == IMAGE_SESSION_GENERATION_MAX_ATTEMPTS
    assert payload["generation_tasks"][0]["is_retryable"] is True
    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, payload["generation_tasks"][0]["id"])
    assert task is not None
    assert task.failure_reason == "Image generation failed, please retry later"
    assert task.attempts == IMAGE_SESSION_GENERATION_MAX_ATTEMPTS
    assert task.is_retryable is True
    assert calls["count"] == IMAGE_SESSION_GENERATION_MAX_ATTEMPTS


def test_image_session_worker_auto_retry_exposes_last_failure_metadata(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.application.image_sessions import (
        IMAGE_SESSION_GENERATION_MAX_ATTEMPTS,
        create_image_session,
        create_image_session_generation_task,
        execute_image_session_generation_task,
    )
    from productflow_backend.domain.enums import JobStatus

    sent: list[str] = []

    def fail_generate(*args, **kwargs) -> None:
        raise TimeoutError("read timeout from provider")

    monkeypatch.setattr(
        "productflow_backend.infrastructure.image.chat_service.ImageChatService.generate",
        fail_generate,
    )
    monkeypatch.setattr(
        "productflow_backend.application.image_sessions.enqueue_image_session_generation_task",
        lambda task_id: sent.append(task_id),
    )

    image_session = create_image_session(db_session, product_id=None, title="auto retry metadata")
    result = create_image_session_generation_task(
        db_session,
        image_session_id=image_session.id,
        prompt="Requeue after first timeout for retry",
        size="1024x1024",
    )

    execute_image_session_generation_task(result.task.id)

    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, result.task.id)
    assert task is not None
    assert task.status == JobStatus.QUEUED
    assert task.failure_reason is None
    assert task.progress_phase == "auto_retry_queued"
    assert task.progress_metadata == {
        "last_failure_reason": "Image provider request timed out; please retry later",
        "last_failure_category": "timeout",
        "last_failure_retryable": True,
        "retry_hint": "retry_later",
        "auto_retry_attempt": 1,
        "max_attempts": IMAGE_SESSION_GENERATION_MAX_ATTEMPTS,
    }
    assert task.is_retryable is True
    assert task.attempts == 1
    assert sent == [result.task.id]


def test_image_session_worker_non_retryable_policy_failure_stops_without_auto_retry(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.application.image_sessions import (
        create_image_session,
        create_image_session_generation_task,
    )
    from productflow_backend.domain.enums import JobStatus
    from productflow_backend.presentation.api import create_app

    sent: list[str] = []

    def fail_generate(*args, **kwargs) -> None:
        raise RuntimeError("Request blocked by content policy")

    monkeypatch.setattr(
        "productflow_backend.infrastructure.image.chat_service.ImageChatService.generate",
        fail_generate,
    )
    image_session = create_image_session(db_session, product_id=None, title="policy rejected")
    result = create_image_session_generation_task(
        db_session,
        image_session_id=image_session.id,
        prompt="policy rejection without auto retry",
        size="1024x1024",
    )
    monkeypatch.setattr(
        "productflow_backend.application.image_sessions.enqueue_image_session_generation_task",
        lambda task_id: sent.append(task_id),
    )

    from productflow_backend.application.image_sessions import execute_image_session_generation_task

    execute_image_session_generation_task(result.task.id)

    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, result.task.id)
    assert task is not None
    assert task.status == JobStatus.FAILED
    assert task.failure_reason == "The image provider rejected this content or safety policy; adjust the prompt or reference image and retry"
    assert task.is_retryable is False
    assert task.attempts == 1
    assert sent == []

    app = create_app()
    client = TestClient(app)
    _login(client)
    retried = client.post(f"/api/image-sessions/{image_session.id}/generation-tasks/{task.id}/retry")
    assert retried.status_code == 400
    assert retried.json()["detail"] == "This generation task cannot be retried"


def test_image_session_worker_non_retryable_parameter_failure_stops_without_auto_retry(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.application.image_sessions import (
        create_image_session,
        create_image_session_generation_task,
        execute_image_session_generation_task,
    )
    from productflow_backend.domain.enums import JobStatus

    sent: list[str] = []

    def fail_generate(*args, **kwargs) -> None:
        raise RuntimeError("unknown parameter: background")

    monkeypatch.setattr(
        "productflow_backend.infrastructure.image.chat_service.ImageChatService.generate",
        fail_generate,
    )
    monkeypatch.setattr(
        "productflow_backend.application.image_sessions.enqueue_image_session_generation_task",
        lambda task_id: sent.append(task_id),
    )

    image_session = create_image_session(db_session, product_id=None, title="parameterreject")
    result = create_image_session_generation_task(
        db_session,
        image_session_id=image_session.id,
        prompt="parameternot supportedno auto retry",
        size="1024x1024",
    )

    execute_image_session_generation_task(result.task.id)

    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, result.task.id)
    assert task is not None
    assert task.status == JobStatus.FAILED
    assert task.failure_reason == "Image provider parameters are not supported; please check size, model, or advanced parameters and retry"
    assert task.is_retryable is False
    assert task.attempts == 1
    assert sent == []


def test_image_session_worker_exposes_safe_provider_failure_detail(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.presentation.api import create_app

    def fail_generate(*args, **kwargs) -> None:
        raise RuntimeError("image2 not supported 64x64，minimum size is 512x512")

    monkeypatch.setattr(
        "productflow_backend.infrastructure.image.chat_service.ImageChatService.generate",
        fail_generate,
    )
    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post("/api/image-sessions", json={"title": "provider safetyfaileddetail"})
    assert created.status_code == 201
    response = client.post(
        f"/api/image-sessions/{created.json()['id']}/generate",
        json={"prompt": "this time provider returns safetyfaileddetail", "size": "1024x1024"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["generation_tasks"][0]["status"] == "failed"
    assert payload["generation_tasks"][0]["failure_reason"] == "Image generation failed: image2 not supported 64x64，minimum size is 512x512"
    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, payload["generation_tasks"][0]["id"])
    assert task is not None
    assert task.failure_reason == "Image generation failed: image2 not supported 64x64，minimum size is 512x512"


def test_image_session_worker_categorizes_wrapped_connection_failure(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.presentation.api import create_app

    def fail_generate(*args, **kwargs) -> None:
        cause = ConnectionError("connection reset by peer")
        wrapped = RuntimeError("image provider request failed，please check provider configuration and retry")
        raise wrapped from cause

    monkeypatch.setattr(
        "productflow_backend.infrastructure.image.chat_service.ImageChatService.generate",
        fail_generate,
    )
    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post("/api/image-sessions", json={"title": "provider interruptfailed"})
    assert created.status_code == 201
    response = client.post(
        f"/api/image-sessions/{created.json()['id']}/generate",
        json={"prompt": "this time provider would interrupt", "size": "1024x1024"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["generation_tasks"][0]["status"] == "failed"
    assert payload["generation_tasks"][0]["failure_reason"] == "Image provider connection interrupted; check network or proxy and retry"
    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, payload["generation_tasks"][0]["id"])
    assert task is not None
    assert task.failure_reason == "Image provider connection interrupted; check network or proxy and retry"


def test_image_session_worker_surfaces_completed_text_without_image_reason(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.application.image_sessions import (
        create_image_session,
        create_image_session_generation_task,
        execute_image_session_generation_task,
    )
    from productflow_backend.infrastructure.image.responses_provider import PROVIDER_TEXT_OUTPUT_MESSAGE

    def fail_with_text_output(*args, **kwargs) -> None:
        raise RuntimeError(PROVIDER_TEXT_OUTPUT_MESSAGE)

    monkeypatch.setattr(
        "productflow_backend.infrastructure.image.chat_service.ImageChatService.generate",
        fail_with_text_output,
    )

    image_session = create_image_session(db_session, product_id=None, title="provider text only")
    result = create_image_session_generation_task(
        db_session,
        image_session_id=image_session.id,
        prompt="providercompleted but returned only text",
        size="1024x1024",
    )

    execute_image_session_generation_task(result.task.id)

    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, result.task.id)
    assert task is not None
    assert task.status == "failed"
    assert task.failure_reason == PROVIDER_TEXT_OUTPUT_MESSAGE
    assert task.is_retryable is True


def test_image_session_worker_partial_retry_continues_remaining_candidates_without_duplicates(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.application.image_sessions import (
        create_image_session,
        create_image_session_generation_task,
        execute_image_session_generation_task,
    )
    from productflow_backend.infrastructure.image.chat_service import GeneratedChatImage

    calls = {"count": 0}

    def generate_then_timeout(self, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise TimeLimitExceeded()
        return GeneratedChatImage(
            bytes_data=_make_demo_image_bytes_with_size(1024, 1024),
            mime_type="image/png",
            model_name="mock-image-chat-v1",
            provider_name="mock",
            prompt_version="test-v1",
            size=kwargs["size"],
            generated_at=datetime.now(UTC),
            provider_request_json={"size": kwargs["size"], "candidate": calls["count"]},
            provider_output_json={},
        )

    monkeypatch.setattr(
        "productflow_backend.infrastructure.image.chat_service.ImageChatService.generate",
        generate_then_timeout,
    )

    image_session = create_image_session(db_session, product_id=None, title="partial success timeout")
    result = create_image_session_generation_task(
        db_session,
        image_session_id=image_session.id,
        prompt="generate twoimage，secondimagetimeout",
        size="1024x1024",
        generation_count=2,
    )

    execute_image_session_generation_task(result.task.id)

    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, result.task.id)
    rounds = (
        db_session.query(ImageSessionRound)
        .filter(ImageSessionRound.session_id == image_session.id)
        .order_by(ImageSessionRound.candidate_index)
        .all()
    )

    assert task is not None
    assert task.status == "succeeded"
    assert task.failure_reason is None
    assert task.result_generation_group_id is not None
    assert task.completed_candidates == 2
    assert task.active_candidate_index is None
    assert task.progress_phase == "succeeded"
    assert task.progress_updated_at is not None
    assert task.finished_at is not None
    assert task.is_retryable is False
    assert task.attempts == 2
    assert calls["count"] == 3
    assert len(rounds) == 2
    assert rounds[0].candidate_index == 1
    assert rounds[0].candidate_count == 2
    assert rounds[0].generation_group_id == task.result_generation_group_id
    assert rounds[1].candidate_index == 2
    assert rounds[1].candidate_count == 2
    assert rounds[1].generation_group_id == task.result_generation_group_id
    assert Path(configured_env, rounds[0].generated_asset.storage_path).exists()


def test_image_session_worker_marks_task_failed_when_time_limit_raises_outside_candidate_loop(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.application.image_sessions import (
        create_image_session,
        create_image_session_generation_task,
        execute_image_session_generation_task,
    )

    monkeypatch.setattr(
        "productflow_backend.application.image_sessions._execute_image_session_round_generation",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeLimitExceeded()),
    )

    image_session = create_image_session(db_session, product_id=None, title="overall timeout")
    result = create_image_session_generation_task(
        db_session,
        image_session_id=image_session.id,
        prompt="timeout before entering candidate loop",
        size="1024x1024",
    )

    execute_image_session_generation_task(result.task.id)

    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, result.task.id)

    assert task is not None
    assert task.status == "failed"
    assert task.failure_reason == "Image generation failed, please retry later"
    assert task.finished_at is not None
    assert task.attempts == 3
    assert task.is_retryable is True


def test_image_session_worker_failure_settles_task_when_parent_session_deleted(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import delete

    from productflow_backend.application.image_sessions import (
        create_image_session,
        create_image_session_generation_task,
        execute_image_session_generation_task,
    )

    monkeypatch.setattr(
        "productflow_backend.infrastructure.image.chat_service.ImageChatService.generate",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("provider failed")),
    )

    image_session = create_image_session(db_session, product_id=None, title="parent session deleted")
    result = create_image_session_generation_task(
        db_session,
        image_session_id=image_session.id,
        prompt="provider failedParent session may no longer exist",
        size="1024x1024",
    )

    db_session.execute(delete(ImageSession).where(ImageSession.id == image_session.id))
    db_session.commit()

    execute_image_session_generation_task(result.task.id)

    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, result.task.id)
    assert task is not None
    assert task.status == "failed"
    assert task.failure_reason == "Image generation failed, please retry later"
    assert task.finished_at is not None
    assert task.attempts == 3
    assert task.is_retryable is True


def test_image_session_worker_failure_settlement_retries_after_stale_data_error(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.orm.exc import StaleDataError

    from productflow_backend.application import image_sessions as image_session_app
    from productflow_backend.application.image_sessions import (
        ImageSessionGenerationExecutionError,
        create_image_session,
        create_image_session_generation_task,
        execute_image_session_generation_task,
    )

    monkeypatch.setattr(
        image_session_app,
        "_execute_image_session_round_generation",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ImageSessionGenerationExecutionError(
                completed_candidates=1,
                requested_candidates=2,
                generation_group_id="generation-group-stale",
                timed_out=False,
            )
        ),
    )
    original_handle_failure = image_session_app._handle_image_generation_task_failure
    settlement_calls = {"count": 0}

    def flaky_handle_failure(*args, **kwargs):
        settlement_calls["count"] += 1
        if settlement_calls["count"] == 1:
            raise StaleDataError("stale parent session")
        return original_handle_failure(*args, **kwargs)

    monkeypatch.setattr(image_session_app, "_handle_image_generation_task_failure", flaky_handle_failure)

    image_session = create_image_session(db_session, product_id=None, title="stale closure")
    result = create_image_session_generation_task(
        db_session,
        image_session_id=image_session.id,
        prompt="failedduring closure ORM stale",
        size="1024x1024",
        generation_count=2,
    )

    execute_image_session_generation_task(result.task.id)

    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, result.task.id)
    assert task is not None
    assert task.status == "failed"
    assert task.failure_reason == "Generated 1/2 candidates; subsequent generation failed. Please re-trigger to fill the remainder."
    assert task.result_generation_group_id is not None
    assert task.finished_at is not None
    assert task.attempts == 3
    assert task.is_retryable is True
    assert settlement_calls["count"] == 4


def test_image_session_worker_persists_provider_progress_heartbeat(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.application.image_sessions import (
        create_image_session,
        create_image_session_generation_task,
        execute_image_session_generation_task,
    )
    from productflow_backend.infrastructure.image.chat_service import GeneratedChatImage

    def generate_with_progress(self, **kwargs):
        kwargs["progress_callback"](
            {
                "provider_response_id": "resp_background",
                "provider_response_status": "in_progress",
                "provider_response": {"id": "resp_background", "status": "in_progress"},
            }
        )
        kwargs["progress_callback"](
            {
                "provider_response_id": "resp_background",
                "provider_response_status": "completed",
                "provider_response": {"id": "resp_background", "status": "completed"},
            }
        )
        return GeneratedChatImage(
            bytes_data=_make_demo_image_bytes_with_size(1024, 1024),
            mime_type="image/png",
            model_name="mock-image-chat-v1",
            provider_name="mock",
            prompt_version="test-v1",
            size=kwargs["size"],
            generated_at=datetime.now(UTC),
            provider_response_id="resp_background",
            provider_request_json={"size": kwargs["size"]},
            provider_output_json={"id": "resp_background", "status": "completed"},
        )

    monkeypatch.setattr(
        "productflow_backend.infrastructure.image.chat_service.ImageChatService.generate",
        generate_with_progress,
    )

    image_session = create_image_session(db_session, product_id=None, title="provider progress")
    result = create_image_session_generation_task(
        db_session,
        image_session_id=image_session.id,
        prompt="provider polling update heartbeat",
        size="1024x1024",
    )

    execute_image_session_generation_task(result.task.id)

    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, result.task.id)

    assert task is not None
    assert task.status == "succeeded"
    assert task.completed_candidates == 1
    assert task.provider_response_id == "resp_background"
    assert task.provider_response_status == "completed"
    assert task.progress_updated_at is not None
    assert task.progress_metadata["candidate_index"] == 1
    assert task.progress_metadata["candidate_count"] == 1
    assert task.progress_metadata["generated_asset_id"]
    assert task.progress_metadata["round_id"]


def test_image_session_worker_duplicate_message_noops_terminal_task(
    configured_env: Path,
    db_session,
) -> None:
    from productflow_backend.application.image_sessions import (
        create_image_session,
        create_image_session_generation_task,
        execute_image_session_generation_task,
    )

    image_session = create_image_session(db_session, product_id=None, title="duplicate message")
    result = create_image_session_generation_task(
        db_session,
        image_session_id=image_session.id,
        prompt="duplicate worker message executes only once",
        size="1024x1024",
    )

    execute_image_session_generation_task(result.task.id)
    execute_image_session_generation_task(result.task.id)

    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, result.task.id)
    rounds = db_session.query(ImageSessionRound).filter(ImageSessionRound.session_id == image_session.id).all()
    assert task is not None
    assert task.status == "succeeded"
    assert len(rounds) == 1


def test_image_session_worker_duplicate_message_noops_running_task(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.application.image_sessions import (
        create_image_session,
        create_image_session_generation_task,
        execute_image_session_generation_task,
    )
    from productflow_backend.domain.enums import JobStatus

    image_session = create_image_session(db_session, product_id=None, title="running duplicate message")
    result = create_image_session_generation_task(
        db_session,
        image_session_id=image_session.id,
        prompt="running status should not run twice",
        size="1024x1024",
    )
    result.task.status = JobStatus.RUNNING
    db_session.commit()
    calls: list[object] = []
    monkeypatch.setattr(
        "productflow_backend.infrastructure.image.chat_service.ImageChatService.generate",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    execute_image_session_generation_task(result.task.id)

    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, result.task.id)
    rounds = db_session.query(ImageSessionRound).filter(ImageSessionRound.session_id == image_session.id).all()
    assert task is not None
    assert task.status == "running"
    assert rounds == []
    assert calls == []


def test_image_session_worker_defers_queued_task_when_global_running_capacity_full(
    configured_env: Path,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from productflow_backend.application.image_sessions import (
        IMAGE_SESSION_CAPACITY_RETRY_DELAY_MS,
        create_image_session,
        execute_image_session_generation_task,
    )
    from productflow_backend.domain.enums import JobStatus
    from productflow_backend.infrastructure.db.models import AppSetting

    image_session = create_image_session(db_session, product_id=None, title="same session concurrency limit")
    running = ImageSessionGenerationTask(
        session_id=image_session.id,
        status=JobStatus.RUNNING,
        prompt="first image is running",
        size="1024x1024",
        generation_count=1,
    )
    queued = ImageSessionGenerationTask(
        session_id=image_session.id,
        status=JobStatus.QUEUED,
        prompt="secondimagemust wait for capacity",
        size="1024x1024",
        generation_count=1,
    )
    db_session.add_all([running, queued])
    db_session.add(AppSetting(key="generation_max_concurrent_tasks", value="1"))
    db_session.commit()

    delayed_requeues: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "productflow_backend.application.image_sessions.enqueue_image_session_generation_task_later",
        lambda task_id, *, delay_ms: delayed_requeues.append((task_id, delay_ms)),
    )
    monkeypatch.setattr(
        "productflow_backend.infrastructure.image.chat_service.ImageChatService.generate",
        lambda *args, **kwargs: pytest.fail("capacity-blocked task must not call provider"),
    )

    execute_image_session_generation_task(queued.id)

    db_session.expire_all()
    persisted = db_session.get(ImageSessionGenerationTask, queued.id)
    rounds = db_session.query(ImageSessionRound).filter(ImageSessionRound.session_id == image_session.id).all()

    assert persisted is not None
    assert persisted.status == JobStatus.QUEUED
    assert persisted.attempts == 0
    assert persisted.progress_phase == "waiting_for_capacity"
    assert persisted.progress_updated_at is not None
    assert rounds == []
    assert delayed_requeues == [(queued.id, IMAGE_SESSION_CAPACITY_RETRY_DELAY_MS)]


def test_image_session_branch_uses_selected_base_and_references_only(configured_env: Path, db_session) -> None:
    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post("/api/image-sessions", json={"title": "branch test"})
    assert created.status_code == 201
    session_id = created.json()["id"]

    first = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "first base image", "size": "1024x1024"},
    )
    assert first.status_code == 202
    first_round = next(round_item for round_item in first.json()["rounds"] if round_item["prompt"] == "first base image")
    first_asset_id = first_round["generated_asset"]["id"]

    upload = client.post(
        f"/api/image-sessions/{session_id}/reference-images",
        files=[
            ("reference_images", ("ref-a.png", _make_demo_image_bytes(), "image/png")),
            ("reference_images", ("ref-b.png", _make_demo_image_bytes(), "image/png")),
        ],
    )
    assert upload.status_code == 200
    reference_ids = [asset["id"] for asset in upload.json()["assets"] if asset["kind"] == "reference_upload"]
    assert len(reference_ids) == 2

    branched = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={
            "prompt": "continue only from the first and second reference images",
            "size": "1024x1024",
            "base_asset_id": first_asset_id,
            "selected_reference_asset_ids": [reference_ids[1]],
            "generation_count": 1,
        },
    )
    assert branched.status_code == 202
    payload = branched.json()
    branch_round = next(
        round_item for round_item in payload["rounds"] if round_item["prompt"] == "continue only from the first and second reference images"
    )
    assert branch_round["base_asset_id"] == first_asset_id
    assert branch_round["selected_reference_asset_ids"] == [reference_ids[1]]
    assert branch_round["previous_response_id"] is None
    assert branch_round["generation_group_id"]
    assert branch_round["candidate_index"] == 1
    assert branch_round["candidate_count"] == 1

    db_session.expire_all()
    persisted = db_session.get(ImageSessionRound, branch_round["id"])
    assert persisted is not None
    assert persisted.base_asset_id == first_asset_id
    assert persisted.selected_reference_asset_ids == [reference_ids[1]]
    assert persisted.provider_request_json == {
        "prompt": "continue only from the first and second reference images",
        "size": "1024x1024",
        "history_count": 0,
        "manual_reference_count": 2,
        "previous_response_id": None,
    }


def test_image_session_openai_images_uses_selected_base_and_references_only(
    configured_env: Path,
    db_session,
    monkeypatch,
) -> None:
    from productflow_backend.application.image_sessions import (
        add_image_session_reference_images,
        create_image_session,
        generate_image_session_round,
    )

    monkeypatch.setenv("IMAGE_PROVIDER_KIND", "openai_images")
    monkeypatch.setenv("IMAGE_API_KEY", "demo-api-key")
    monkeypatch.setenv("IMAGE_GENERATE_MODEL", "gpt-image-1")
    get_settings.cache_clear()

    calls: list[dict] = []

    class DummyItem:
        b64_json = b64encode(_make_demo_image_bytes()).decode("utf-8")
        revised_prompt = None

    class DummyResponse:
        data = [DummyItem()]

    class DummyImages:
        def generate(self, **kwargs):
            calls.append({"method": "generate", **kwargs})
            return DummyResponse()

        def edit(self, **kwargs):
            calls.append({"method": "edit", **kwargs})
            return DummyResponse()

    class DummyOpenAI:
        def __init__(self, **kwargs) -> None:
            self.images = DummyImages()

    monkeypatch.setattr("productflow_backend.infrastructure.image.images_provider.OpenAI", DummyOpenAI)

    image_session = create_image_session(db_session, product_id=None, title="Images API branch test")
    first = generate_image_session_round(
        db_session,
        image_session_id=image_session.id,
        prompt="first base image",
        size="1024x1024",
    )
    first_asset_id = first.rounds[-1].generated_asset_id
    assert first_asset_id is not None

    updated = add_image_session_reference_images(
        db_session,
        image_session_id=image_session.id,
        reference_image_uploads=[
            (_make_demo_image_bytes(), "ref-a.png", "image/png"),
            (_make_demo_image_bytes(), "ref-b.png", "image/png"),
        ],
    )
    reference_ids = [asset.id for asset in updated.assets if asset.kind.value == "reference_upload"]
    assert len(reference_ids) == 2

    branch_prompt = "continue only from the first and second reference images"
    branched = generate_image_session_round(
        db_session,
        image_session_id=image_session.id,
        prompt=branch_prompt,
        size="1024x1024",
        base_asset_id=first_asset_id,
        selected_reference_asset_ids=[reference_ids[1]],
        generation_count=1,
    )
    branch_round = next(round_item for round_item in branched.rounds if round_item.prompt == branch_prompt)
    assert branch_round.provider_name == "openai-images"
    assert branch_round.base_asset_id == first_asset_id
    assert branch_round.selected_reference_asset_ids == [reference_ids[1]]

    assert calls[0]["method"] == "generate"
    assert calls[1]["method"] == "edit"
    assert [image.name for image in calls[1]["image"]] == ["base.png", "reference-1.png"]
    assert "previous_response_id" not in calls[1]

    db_session.expire_all()
    persisted = db_session.get(ImageSessionRound, branch_round.id)
    assert persisted is not None
    assert persisted.provider_request_json["image_count"] == 2
    assert persisted.provider_request_json["images"] == [
        {"filename": "base.png", "mime_type": "image/png"},
        {"filename": "reference-1.png", "mime_type": "image/png"},
    ]
    assert persisted.provider_output_json["_productflow"]["requested_image_count"] == 2
    assert persisted.provider_output_json["_productflow"]["effective_image_count"] == 2


def test_image_session_google_gemini_uses_selected_base_and_references_only(
    configured_env: Path,
    db_session,
    monkeypatch,
) -> None:
    from productflow_backend.application.image_sessions import (
        add_image_session_reference_images,
        create_image_session,
        generate_image_session_round,
    )

    profile = ProviderProfile(
        name="Gemini",
        provider_type="google_gemini",
        base_url=None,
        api_key="google-api-key",
        capabilities_json=["image_google_gemini"],
        default_models_json={},
        config_json={},
        enabled=True,
    )
    db_session.add(profile)
    db_session.flush()
    db_session.add(
        ProviderBinding(
            purpose="image",
            provider_kind="google_gemini_image",
            provider_profile_id=profile.id,
            model_settings_json={"model": "gemini-2.5-flash-image"},
            config_json={"gemini_api_version": "v1beta"},
        )
    )
    db_session.commit()

    calls: list[dict] = []

    def fake_generate_image(self, *, prompt, size, reference_images=None):
        references = reference_images or []
        calls.append({"prompt": prompt, "size": size, "references": references})
        return SimpleNamespace(
            bytes_data=_make_demo_image_bytes(),
            mime_type="image/png",
            model_name=self.model,
            provider_name="google-gemini-image",
            prompt_version="gemini-generate-content-image-v1",
            size=size,
            generated_at=datetime.now(UTC),
            provider_response_id="gemini-response",
            provider_request_json={
                "prompt": prompt,
                "size": size,
                "reference_image_count": len(references),
                "reference_images": [
                    {"filename": reference.filename, "mime_type": reference.mime_type} for reference in references
                ],
            },
            provider_output_json={"_productflow": {"model": self.model}},
        )

    monkeypatch.setattr(
        "productflow_backend.infrastructure.image.gemini_provider.GoogleGeminiImageClient.generate_image",
        fake_generate_image,
    )

    image_session = create_image_session(db_session, product_id=None, title="Gemini branch test")
    first = generate_image_session_round(
        db_session,
        image_session_id=image_session.id,
        prompt="first base image",
        size="1024x1024",
    )
    first_asset_id = first.rounds[-1].generated_asset_id
    assert first_asset_id is not None

    updated = add_image_session_reference_images(
        db_session,
        image_session_id=image_session.id,
        reference_image_uploads=[
            (_make_demo_image_bytes(), "ref-a.png", "image/png"),
            (_make_demo_image_bytes(), "ref-b.png", "image/png"),
        ],
    )
    reference_ids = [asset.id for asset in updated.assets if asset.kind.value == "reference_upload"]
    assert len(reference_ids) == 2

    branch_prompt = "continue only from the first and second reference images"
    branched = generate_image_session_round(
        db_session,
        image_session_id=image_session.id,
        prompt=branch_prompt,
        size="1024x1024",
        base_asset_id=first_asset_id,
        selected_reference_asset_ids=[reference_ids[1]],
        generation_count=1,
    )
    branch_round = next(round_item for round_item in branched.rounds if round_item.prompt == branch_prompt)
    assert branch_round.provider_name == "google-gemini-image"
    assert branch_round.base_asset_id == first_asset_id
    assert branch_round.selected_reference_asset_ids == [reference_ids[1]]

    assert len(calls) == 2
    assert calls[0]["references"] == []
    assert [reference.filename for reference in calls[1]["references"]] == ["base.png", "reference-1.png"]

    db_session.expire_all()
    persisted = db_session.get(ImageSessionRound, branch_round.id)
    assert persisted is not None
    assert persisted.provider_request_json["reference_image_count"] == 2
    assert persisted.provider_request_json["reference_images"] == [
        {"filename": "base.png", "mime_type": "image/png"},
        {"filename": "reference-1.png", "mime_type": "image/png"},
    ]


def test_image_session_branch_validates_asset_scope_and_kind(configured_env: Path) -> None:
    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post("/api/image-sessions", json={"title": "validate"})
    other_created = client.post("/api/image-sessions", json={"title": "other session"})
    assert created.status_code == 201
    assert other_created.status_code == 201
    session_id = created.json()["id"]
    other_session_id = other_created.json()["id"]

    generated = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "generated image", "size": "1024x1024"},
    )
    other_generated = client.post(
        f"/api/image-sessions/{other_session_id}/generate",
        json={"prompt": "othergenerated image", "size": "1024x1024"},
    )
    assert generated.status_code == 202
    assert other_generated.status_code == 202
    generated_asset_id = generated.json()["rounds"][-1]["generated_asset"]["id"]
    other_generated_asset_id = other_generated.json()["rounds"][-1]["generated_asset"]["id"]

    upload = client.post(
        f"/api/image-sessions/{session_id}/reference-images",
        files={"reference_images": ("ref.png", _make_demo_image_bytes(), "image/png")},
    )
    other_upload = client.post(
        f"/api/image-sessions/{other_session_id}/reference-images",
        files={"reference_images": ("other-ref.png", _make_demo_image_bytes(), "image/png")},
    )
    assert upload.status_code == 200
    assert other_upload.status_code == 200
    reference_asset_id = next(asset["id"] for asset in upload.json()["assets"] if asset["kind"] == "reference_upload")
    other_reference_asset_id = next(
        asset["id"] for asset in other_upload.json()["assets"] if asset["kind"] == "reference_upload"
    )

    base_wrong_session = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "wrong session base image", "size": "1024x1024", "base_asset_id": other_generated_asset_id},
    )
    assert base_wrong_session.status_code == 404
    assert base_wrong_session.json()["detail"] == "Session image not found"

    base_wrong_kind = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "wrong type base image", "size": "1024x1024", "base_asset_id": reference_asset_id},
    )
    assert base_wrong_kind.status_code == 400
    assert base_wrong_kind.json()["detail"] == "Can only continue from a session-generated image"

    reference_wrong_session = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={
            "prompt": "wrong sessionreference image",
            "size": "1024x1024",
            "base_asset_id": generated_asset_id,
            "selected_reference_asset_ids": [other_reference_asset_id],
        },
    )
    assert reference_wrong_session.status_code == 404
    assert reference_wrong_session.json()["detail"] == "Session reference image not found"

    reference_wrong_kind = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={
            "prompt": "wrong typereference image",
            "size": "1024x1024",
            "base_asset_id": generated_asset_id,
            "selected_reference_asset_ids": [generated_asset_id],
        },
    )
    assert reference_wrong_kind.status_code == 400
    assert reference_wrong_kind.json()["detail"] == "Only session reference images can be selected for this round"

    too_many_upload = client.post(
        f"/api/image-sessions/{session_id}/reference-images",
        files=[
            ("reference_images", (f"ref-{index}.png", _make_demo_image_bytes(), "image/png"))
            for index in range(6)
        ],
    )
    assert too_many_upload.status_code == 200
    reference_ids = [
        asset["id"] for asset in too_many_upload.json()["assets"] if asset["kind"] == "reference_upload"
    ][-6:]
    too_many = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={
            "prompt": "context too large",
            "size": "1024x1024",
            "base_asset_id": generated_asset_id,
            "selected_reference_asset_ids": reference_ids,
        },
    )
    assert too_many.status_code == 400
    assert too_many.json()["detail"] == "This round allows at most 6 image-context (including the branch base image)"

    bad_count = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "invalid count", "size": "1024x1024", "generation_count": 11},
    )
    assert bad_count.status_code == 422


def test_image_session_multi_candidate_generation_persists_one_round_per_candidate(
    configured_env: Path,
    db_session,
) -> None:
    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post("/api/image-sessions", json={"title": "multi candidate"})
    assert created.status_code == 201
    session_id = created.json()["id"]

    generated = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "samepromptproduce threecandidates", "size": "1024x1024", "generation_count": 3},
    )
    assert generated.status_code == 202
    rounds = generated.json()["rounds"]
    assert len(rounds) == 3
    group_ids = {round_item["generation_group_id"] for round_item in rounds}
    assert len(group_ids) == 1
    assert [round_item["candidate_index"] for round_item in rounds] == [1, 2, 3]
    assert all(round_item["candidate_count"] == 3 for round_item in rounds)
    assert len({round_item["generated_asset"]["id"] for round_item in rounds}) == 3

    db_session.expire_all()
    persisted_rounds = (
        db_session.query(ImageSessionRound)
        .filter(ImageSessionRound.session_id == session_id)
        .order_by(ImageSessionRound.candidate_index)
        .all()
    )
    assert len(persisted_rounds) == 3
    assert {round_item.generation_group_id for round_item in persisted_rounds} == group_ids
    assert [round_item.candidate_index for round_item in persisted_rounds] == [1, 2, 3]


def test_image_session_openai_images_candidate_count_sets_provider_batch_n(
    configured_env: Path,
    db_session,
    monkeypatch,
) -> None:
    from productflow_backend.presentation.api import create_app

    monkeypatch.setenv("IMAGE_PROVIDER_KIND", "openai_images")
    monkeypatch.setenv("IMAGE_API_KEY", "demo-api-key")
    monkeypatch.setenv("IMAGE_GENERATE_MODEL", "gpt-image-1")
    get_settings.cache_clear()
    db_session.add(
        AppSetting(
            key="image_tool_allowed_fields",
            value="model,quality,output_format,output_compression,moderation,action,input_fidelity,partial_images,n",
        )
    )
    db_session.commit()

    calls: list[dict] = []
    encoded_result = b64encode(_make_demo_image_bytes()).decode("utf-8")

    class DummyItem:
        def __init__(self, index: int) -> None:
            self.b64_json = encoded_result
            self.revised_prompt = f"revised-{index}"

    class DummyResponse:
        data = [DummyItem(index) for index in range(1, 11)]

    class DummyImages:
        def generate(self, **kwargs):
            calls.append({"method": "generate", **kwargs})
            return DummyResponse()

    class DummyOpenAI:
        def __init__(self, **kwargs) -> None:
            self.images = DummyImages()

    monkeypatch.setattr("productflow_backend.infrastructure.image.images_provider.OpenAI", DummyOpenAI)

    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post("/api/image-sessions", json={"title": "Images API n"})
    assert created.status_code == 201
    generated = client.post(
        f"/api/image-sessions/{created.json()['id']}/generate",
        json={"prompt": "same request returns ten candidates", "size": "1024x1024", "generation_count": 10},
    )

    assert generated.status_code == 202
    assert calls == [
        {
            "method": "generate",
            "model": "gpt-image-1",
            "prompt": calls[0]["prompt"],
            "size": "1024x1024",
            "n": 10,
            "response_format": "b64_json",
        }
    ]
    payload = generated.json()
    assert payload["generation_tasks"][0]["generation_count"] == 10
    assert payload["generation_tasks"][0]["tool_options"] is None
    rounds = payload["rounds"]
    assert len(rounds) == 10
    assert [round_item["candidate_index"] for round_item in rounds] == list(range(1, 11))
    assert all(round_item["candidate_count"] == 10 for round_item in rounds)
    assert len({round_item["generated_asset"]["id"] for round_item in rounds}) == 10

    db_session.expire_all()
    task = db_session.get(ImageSessionGenerationTask, payload["generation_tasks"][0]["id"])
    assert task is not None
    assert task.generation_count == 10
    persisted_rounds = (
        db_session.query(ImageSessionRound)
        .filter(ImageSessionRound.session_id == created.json()["id"])
        .order_by(ImageSessionRound.candidate_index)
        .all()
    )
    assert [round_item.candidate_index for round_item in persisted_rounds] == list(range(1, 11))


def test_image_session_worker_actor_uses_internal_failsafe_time_limit(configured_env: Path) -> None:
    from productflow_backend.workers import (
        IMAGE_SESSION_WORKER_FAILSAFE_TIME_LIMIT_MS,
        get_image_session_worker_failsafe_time_limit_ms,
        run_image_session_generation_task,
    )

    assert get_image_session_worker_failsafe_time_limit_ms() == 24 * 60 * 60 * 1000
    assert IMAGE_SESSION_WORKER_FAILSAFE_TIME_LIMIT_MS == 24 * 60 * 60 * 1000
    assert run_image_session_generation_task.options["time_limit"] == IMAGE_SESSION_WORKER_FAILSAFE_TIME_LIMIT_MS


def test_image_session_generation_accepts_custom_size_and_rejects_invalid_dimensions(configured_env: Path) -> None:
    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)

    _login(client)

    created = client.post("/api/image-sessions", json={"title": "custom size"})
    assert created.status_code == 201
    session_id = created.json()["id"]

    generated = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "make oneimage 16:9 showcase image", "size": "1280x720"},
    )
    assert generated.status_code == 202
    assert generated.json()["rounds"][-1]["size"] == "1280x720"
    generated_asset_id = generated.json()["rounds"][-1]["generated_asset"]["id"]

    non_multiple = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "provider 16 multiple calibration", "size": "1500x800", "base_asset_id": generated_asset_id},
    )
    assert non_multiple.status_code == 202
    assert non_multiple.json()["rounds"][-1]["size"] == "1504x800"

    undersized = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "provider lower bound fallback", "size": "64x64", "base_asset_id": generated_asset_id},
    )
    assert undersized.status_code == 202
    assert undersized.json()["rounds"][-1]["size"] == "512x512"

    zero = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "invalid size", "size": "0x720"},
    )
    assert zero.status_code == 422
    assert "width and height must be greater than 0" in zero.text

    oversized = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "size too large", "size": "5000x5000", "base_asset_id": generated_asset_id},
    )
    assert oversized.status_code == 202
    assert oversized.json()["rounds"][-1]["size"] == "3840x3840"


def test_image_session_reference_image_can_be_deleted(configured_env: Path, db_session) -> None:
    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post("/api/image-sessions", json={"title": "reference imagedelete"})
    assert created.status_code == 201
    session_id = created.json()["id"]
    upload = client.post(
        f"/api/image-sessions/{session_id}/reference-images",
        files={"reference_images": ("sample.png", _make_demo_image_bytes(), "image/png")},
    )
    assert upload.status_code == 200
    reference_asset = next(asset for asset in upload.json()["assets"] if asset["kind"] == "reference_upload")

    db_session.expire_all()
    persisted_asset = db_session.get(ImageSessionAsset, reference_asset["id"])
    assert persisted_asset is not None
    reference_path = Path(configured_env) / persisted_asset.storage_path
    assert reference_path.exists()

    deleted = client.delete(f"/api/image-sessions/{session_id}/reference-images/{reference_asset['id']}")
    assert deleted.status_code == 200
    assert all(asset["id"] != reference_asset["id"] for asset in deleted.json()["assets"])

    db_session.expire_all()
    assert db_session.get(ImageSessionAsset, reference_asset["id"]) is None
    assert not reference_path.exists()

def test_image_session_can_be_deleted_with_files(configured_env: Path, db_session) -> None:
    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)
    _login(client)

    created = client.post("/api/image-sessions", json={"title": "full session delete"})
    assert created.status_code == 201
    session_id = created.json()["id"]
    upload = client.post(
        f"/api/image-sessions/{session_id}/reference-images",
        files={"reference_images": ("sample.png", _make_demo_image_bytes(), "image/png")},
    )
    assert upload.status_code == 200
    generated = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "create a white background product image", "size": "1024x1024"},
    )
    assert generated.status_code == 202

    db_session.expire_all()
    asset_paths = [
        Path(configured_env) / asset.storage_path
        for asset in db_session.query(ImageSessionAsset).filter(ImageSessionAsset.session_id == session_id).all()
    ]
    assert asset_paths
    assert all(path.exists() for path in asset_paths)
    session_root = Path(configured_env) / "image_sessions" / session_id
    assert session_root.exists()

    _enable_deletion(client)
    deleted = client.delete(f"/api/image-sessions/{session_id}")
    assert deleted.status_code == 204

    listed = client.get("/api/image-sessions")
    assert listed.status_code == 200
    assert all(item["id"] != session_id for item in listed.json()["items"])

    db_session.expire_all()
    assert db_session.get(ImageSession, session_id) is None
    assert all(not path.exists() for path in asset_paths)
    assert not session_root.exists()

def test_image_session_result_can_write_back_to_product(configured_env: Path) -> None:
    from productflow_backend.presentation.api import create_app

    app = create_app()
    client = TestClient(app)
    _login(client)

    create_product_response = client.post(
        "/api/products",
        data={"name": "hand cream", "category": "personal care", "price": "59.00"},
        files={"image": ("cream.png", _make_demo_image_bytes(), "image/png")},
    )
    assert create_product_response.status_code == 201
    product_id = create_product_response.json()["id"]

    created = client.post("/api/image-sessions", json={"product_id": product_id})
    assert created.status_code == 201
    session_id = created.json()["id"]

    generated = client.post(
        f"/api/image-sessions/{session_id}/generate",
        json={"prompt": "make oneimageheightlevel bathroom counterhand creamad image", "size": "1024x1024"},
    )
    assert generated.status_code == 202
    generated_payload = generated.json()
    generated_asset_id = generated_payload["rounds"][-1]["generated_asset"]["id"]

    attach_reference = client.post(
        f"/api/image-sessions/{session_id}/assets/{generated_asset_id}/attach-to-product",
        json={"target": "reference"},
    )
    assert attach_reference.status_code == 200
    assert attach_reference.json()["message"] == "Added to product reference images"

    product_after_reference = client.get(f"/api/products/{product_id}")
    assert product_after_reference.status_code == 200
    reference_assets = [
        asset for asset in product_after_reference.json()["source_assets"] if asset["kind"] == "reference_image"
    ]
    assert len(reference_assets) >= 1

    attach_main = client.post(
        f"/api/image-sessions/{session_id}/assets/{generated_asset_id}/attach-to-product",
        json={"target": "main_source"},
    )
    assert attach_main.status_code == 200
    assert attach_main.json()["message"] == "Set as product main image"

    product_after_main = client.get(f"/api/products/{product_id}")
    assert product_after_main.status_code == 200
    original_assets = [
        asset for asset in product_after_main.json()["source_assets"] if asset["kind"] == "original_image"
    ]
    all_reference_assets = [
        asset for asset in product_after_main.json()["source_assets"] if asset["kind"] == "reference_image"
    ]
    assert len(original_assets) == 1
    assert len(all_reference_assets) >= 2
