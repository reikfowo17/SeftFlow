from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from productflow_backend.application.image_sessions import create_image_session, create_image_session_generation_task
from productflow_backend.domain.enums import JobStatus
from productflow_backend.infrastructure.db.models import AppSetting
from productflow_backend.infrastructure.queue import recover_unfinished_image_session_generation_tasks


def test_recover_unfinished_image_session_generation_tasks_requeues_queued_tasks(
    db_session,
    configured_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_session = create_image_session(db_session, product_id=None, title="queued recover")
    result = create_image_session_generation_task(
        db_session,
        image_session_id=image_session.id,
        prompt="queued task should be resent",
        size="1024x1024",
    )
    sent: list[str] = []
    monkeypatch.setattr(
        "productflow_backend.infrastructure.queue.enqueue_image_session_generation_task",
        lambda task_id: sent.append(task_id),
    )

    summary = recover_unfinished_image_session_generation_tasks()

    assert summary.queued_tasks == 1
    assert summary.stale_running_tasks == 0
    assert summary.enqueued_tasks == 1
    assert sent == [result.task.id]


def test_recover_unfinished_image_session_generation_tasks_resets_stale_running_tasks(
    db_session,
    configured_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_session = create_image_session(db_session, product_id=None, title="running recover")
    result = create_image_session_generation_task(
        db_session,
        image_session_id=image_session.id,
        prompt="stale running task should reset",
        size="1024x1024",
    )
    result.task.status = JobStatus.RUNNING
    result.task.started_at = datetime.now(UTC) - timedelta(hours=2)
    result.task.progress_updated_at = datetime.now(UTC) - timedelta(hours=2)
    db_session.commit()
    sent: list[str] = []
    monkeypatch.setattr(
        "productflow_backend.infrastructure.queue.enqueue_image_session_generation_task",
        lambda task_id: sent.append(task_id),
    )

    summary = recover_unfinished_image_session_generation_tasks(
        reset_stale_running=True,
        stale_running_after=timedelta(minutes=30),
    )
    db_session.refresh(result.task)

    assert summary.queued_tasks == 0
    assert summary.stale_running_tasks == 1
    assert summary.enqueued_tasks == 1
    assert sent == [result.task.id]
    assert result.task.status == JobStatus.QUEUED
    assert result.task.started_at is None
    assert result.task.progress_phase == "requeued_after_idle"


def test_recover_unfinished_image_session_generation_tasks_uses_progress_heartbeat_for_stale_running(
    db_session,
    configured_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_session = create_image_session(db_session, product_id=None, title="heartbeat recover")
    result = create_image_session_generation_task(
        db_session,
        image_session_id=image_session.id,
        prompt="started_at old，but progress new，should not reset",
        size="1024x1024",
    )
    result.task.status = JobStatus.RUNNING
    result.task.started_at = datetime.now(UTC) - timedelta(hours=2)
    result.task.progress_updated_at = datetime.now(UTC) - timedelta(minutes=5)
    db_session.commit()
    sent: list[str] = []
    monkeypatch.setattr(
        "productflow_backend.infrastructure.queue.enqueue_image_session_generation_task",
        lambda task_id: sent.append(task_id),
    )

    summary = recover_unfinished_image_session_generation_tasks(
        reset_stale_running=True,
        stale_running_after=timedelta(minutes=30),
    )
    db_session.refresh(result.task)

    assert summary.stale_running_tasks == 0
    assert summary.enqueued_tasks == 0
    assert sent == []
    assert result.task.status == JobStatus.RUNNING


def test_recover_unfinished_image_session_generation_tasks_fails_stale_partial_task(
    db_session,
    configured_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_session = create_image_session(db_session, product_id=None, title="partial heartbeat recover")
    result = create_image_session_generation_task(
        db_session,
        image_session_id=image_session.id,
        prompt="generatedoneimagethen idle",
        size="1024x1024",
        generation_count=2,
    )
    result.task.status = JobStatus.RUNNING
    result.task.started_at = datetime.now(UTC) - timedelta(hours=2)
    result.task.progress_updated_at = datetime.now(UTC) - timedelta(hours=2)
    result.task.completed_candidates = 1
    result.task.result_generation_group_id = "group-partial"
    db_session.commit()
    sent: list[str] = []
    monkeypatch.setattr(
        "productflow_backend.infrastructure.queue.enqueue_image_session_generation_task",
        lambda task_id: sent.append(task_id),
    )

    summary = recover_unfinished_image_session_generation_tasks(
        reset_stale_running=True,
        stale_running_after=timedelta(minutes=30),
    )
    db_session.refresh(result.task)

    assert summary.queued_tasks == 0
    assert summary.stale_running_tasks == 1
    assert summary.enqueued_tasks == 0
    assert sent == []
    assert result.task.status == JobStatus.FAILED
    assert result.task.is_retryable is False
    assert result.task.failure_reason == "Generated 1/2 candidates, but the task timed out and the remaining candidates were not produced."
    assert result.task.progress_phase == "failed_idle_timeout"


def test_recover_unfinished_image_session_generation_tasks_uses_runtime_stale_cutoff_by_default(
    db_session,
    configured_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_session = create_image_session(db_session, product_id=None, title="runtime cutoff recover")
    result = create_image_session_generation_task(
        db_session,
        image_session_id=image_session.id,
        prompt="default should not reset within 90 minutes",
        size="1024x1024",
    )
    result.task.status = JobStatus.RUNNING
    result.task.started_at = datetime.now(UTC) - timedelta(minutes=60)
    db_session.commit()
    sent: list[str] = []
    monkeypatch.setattr(
        "productflow_backend.infrastructure.queue.enqueue_image_session_generation_task",
        lambda task_id: sent.append(task_id),
    )

    default_summary = recover_unfinished_image_session_generation_tasks(reset_stale_running=True)
    db_session.refresh(result.task)

    assert default_summary.stale_running_tasks == 0
    assert default_summary.enqueued_tasks == 0
    assert sent == []
    assert result.task.status == JobStatus.RUNNING

    db_session.add(AppSetting(key="image_session_stale_running_after_minutes", value="30"))
    db_session.commit()

    override_summary = recover_unfinished_image_session_generation_tasks(reset_stale_running=True)
    db_session.refresh(result.task)

    assert override_summary.stale_running_tasks == 1
    assert override_summary.enqueued_tasks == 1
    assert sent == [result.task.id]
    assert result.task.status == JobStatus.QUEUED
    assert result.task.started_at is None
