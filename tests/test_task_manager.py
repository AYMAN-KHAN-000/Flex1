from __future__ import annotations

import json
import os
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import QApplication

from flex.config import Settings
from flex.tasks.task_manager import TaskManager, TaskStorageError
from flex.tasks.task_tools import create_task_manager_tool
from flex.ui.edge_overlay import FlexEdgeOverlay
from flex.ui.main_window import FlexMainWindow


def _manager(tmp_path, *, auto_cleanup=True):
    return TaskManager(
        tmp_path / "tasks.json",
        default_timezone="Asia/Dhaka",
        auto_cleanup=auto_cleanup,
    )


def test_tasks_persist_across_manager_instances(tmp_path):
    task = _manager(tmp_path).create_task(
        "Study",
        "2026-11-10T17:00:00",
        "2026-11-10T20:00:00",
    )["task"]

    reloaded = _manager(tmp_path).get_tasks()

    assert len(reloaded) == 1
    assert reloaded[0]["id"] == task["id"]
    assert reloaded[0]["timezone"] == "Asia/Dhaka"
    assert datetime.fromisoformat(reloaded[0]["start"]).utcoffset() is not None


def test_repeated_create_does_not_duplicate_task(tmp_path):
    manager = _manager(tmp_path)
    first = manager.create_task("Study", "2026-11-10T17:00:00")
    repeated = manager.create_task("study", "2026-11-10T17:00:00")

    assert repeated["duplicate"] is True
    assert first["task"]["id"] == repeated["task"]["id"]
    assert len(manager.get_tasks()) == 1


def test_duplicate_task_detection_uses_same_instant_across_offsets(tmp_path):
    manager = TaskManager(tmp_path / "tasks.json", default_timezone="Asia/Dhaka")
    first = manager.create_task(
        "Study", "2026-11-10T17:00:00+06:00"
    )
    repeated = manager.create_task(
        "study", "2026-11-10T11:00:00+00:00"
    )

    assert repeated["duplicate"] is True
    assert repeated["task"]["id"] == first["task"]["id"]


def test_concurrent_task_creations_are_not_lost(tmp_path):
    manager = _manager(tmp_path)
    tasks = (
        ("Study", "2026-11-10T17:00:00"),
        ("Exercise", "2026-11-10T18:00:00"),
        ("Reading", "2026-11-10T19:00:00"),
    )
    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(lambda item: manager.create_task(*item), tasks))
    assert len(manager.get_tasks()) == 3


def test_invalid_times_and_timezones_are_rejected(tmp_path):
    manager = _manager(tmp_path)
    with pytest.raises(ValueError, match="after its start"):
        manager.create_task(
            "Study",
            "2026-11-10T20:00:00",
            "2026-11-10T17:00:00",
        )
    with pytest.raises(ValueError, match="Unknown IANA timezone"):
        manager.create_task("Study", "2026-11-10T17:00:00", timezone="Bad/Zone")


def test_dst_gap_and_ambiguous_time_need_clarification(tmp_path):
    manager = TaskManager(tmp_path / "tasks.json", default_timezone="America/New_York")

    with pytest.raises(ValueError, match="does not exist"):
        manager.create_task("Study", "2026-03-08T02:30:00")
    with pytest.raises(ValueError, match="ambiguous"):
        manager.create_task("Study", "2026-11-01T01:30:00")
    task = manager.create_task("Study", "2026-11-01T01:30:00-04:00")["task"]
    assert task["start"].endswith("-04:00")


def test_explicit_dst_fallback_offsets_are_compared_as_instants(tmp_path):
    manager = TaskManager(tmp_path / "tasks.json", default_timezone="America/New_York")

    task = manager.create_task(
        "Study",
        "2026-11-01T01:30:00-04:00",
        "2026-11-01T01:15:00-05:00",
    )["task"]

    assert task["end"].endswith("-05:00")


def test_corrupt_storage_is_not_overwritten(tmp_path):
    path = tmp_path / "tasks.json"
    original = "{bad json"
    path.write_text(original, encoding="utf-8")
    manager = TaskManager(path)

    with pytest.raises(TaskStorageError):
        manager.get_tasks()
    with pytest.raises(TaskStorageError):
        manager.create_task("Study", "2026-11-10T17:00:00")
    assert path.read_text(encoding="utf-8") == original


def test_due_events_are_delivered_once_and_cleaned_up(tmp_path):
    manager = _manager(tmp_path)
    task = manager.create_task(
        "Study",
        "2026-11-10T17:00:00",
        "2026-11-10T20:00:00",
    )["task"]
    now = datetime(2026, 11, 10, 11, 30, tzinfo=ZoneInfo("UTC"))
    delivered = []

    def enqueue(_task, kind):
        delivered.append(kind)
        return True

    before_start = datetime(2026, 11, 10, 10, 59, tzinfo=ZoneInfo("UTC"))
    assert manager.process_due_tasks(enqueue, now=before_start) == []
    assert delivered == []
    assert manager.get_tasks()[0]["status"] == "scheduled"

    start_events = manager.process_due_tasks(enqueue, now=now)
    assert [event["type"] for event in start_events] == ["started"]
    assert manager.get_tasks()[0]["status"] == "active"
    assert manager.process_due_tasks(enqueue, now=now) == []

    end_events = manager.process_due_tasks(
        enqueue,
        now=datetime(2026, 11, 10, 14, 30, tzinfo=ZoneInfo("UTC")),
    )
    assert [event["type"] for event in end_events] == ["ended"]
    assert manager.get_tasks() == []
    assert delivered == ["started", "ended"]
    assert task["id"]


def test_failed_notification_enqueue_retries_without_marking_delivered(tmp_path):
    manager = _manager(tmp_path)
    manager.create_task("Study", "2026-11-10T17:00:00")
    now = datetime(2026, 11, 10, 11, 0, tzinfo=ZoneInfo("UTC"))

    assert manager.process_due_tasks(lambda _task, _kind: False, now=now) == []
    assert manager.get_tasks()[0]["start_notified"] is False
    events = manager.process_due_tasks(lambda _task, _kind: True, now=now)
    assert [event["type"] for event in events] == ["started"]
    assert manager.get_tasks() == []


def test_pausing_resuming_and_canceling_tasks(tmp_path):
    manager = _manager(tmp_path)
    task = manager.create_task(
        "Study", "2026-11-10T17:00:00", "2026-11-10T20:00:00"
    )["task"]

    manager.pause_task(task["id"])
    assert manager.get_tasks()[0]["status"] == "paused"
    manager.resume_task(
        task["id"],
        now=datetime(2026, 11, 10, 10, 0, tzinfo=ZoneInfo("UTC")),
    )
    assert manager.get_tasks()[0]["status"] == "scheduled"
    manager.cancel_task(task["id"])
    assert manager.get_tasks()[0]["status"] == "cancelled"


def test_notification_setting_edit_does_not_repeat_delivered_start(tmp_path):
    manager = _manager(tmp_path)
    task = manager.create_task(
        "Study", "2026-11-10T17:00:00", "2026-11-10T20:00:00"
    )["task"]
    now = datetime(2026, 11, 10, 11, 0, tzinfo=ZoneInfo("UTC"))
    manager.process_due_tasks(lambda _task, _kind: True, now=now)

    manager.edit_task(task["id"], notify_end=False)

    assert manager.get_tasks()[0]["start_notified"] is True
    assert manager.process_due_tasks(
        lambda _task, _kind: pytest.fail("Start notification repeated"),
        now=now,
    ) == []


def test_overlapping_tasks_are_saved_with_warning(tmp_path):
    manager = _manager(tmp_path)
    manager.create_task(
        "Study", "2026-11-10T17:00:00", "2026-11-10T20:00:00"
    )
    result = manager.create_task(
        "Exercise", "2026-11-10T19:00:00", "2026-11-10T21:00:00"
    )
    assert result["warnings"] == ["Overlaps with 'Study'."]
    assert len(manager.get_tasks()) == 2


def test_manage_tasks_tool_runs_all_task_actions_locally(tmp_path):
    manager = _manager(tmp_path)
    tool = create_task_manager_tool(manager)
    created = tool.executor(
        action="create",
        title="Programming",
        start="2026-11-10T18:00:00",
        end="2026-11-10T20:00:00",
    )
    task_id = created["task"]["id"]
    assert tool.executor(action="list")["tasks"][0]["id"] == task_id
    tool.executor(action="edit", task_id=task_id, title="Coding")
    tool.executor(action="pause", task_id=task_id)
    tool.executor(action="resume", task_id=task_id)
    tool.executor(
        action="set_notifications",
        task_id=task_id,
        notify_start=False,
        notify_end=False,
    )
    tool.executor(action="cancel", task_id=task_id)
    assert manager.get_tasks()[0]["status"] == "cancelled"
    tool.executor(action="delete", task_id=task_id)
    assert manager.get_tasks() == []


def test_storage_uses_task_list_shape(tmp_path):
    manager = _manager(tmp_path)
    manager.create_task("Study", "2026-11-10T17:00:00")

    data = json.loads((tmp_path / "tasks.json").read_text(encoding="utf-8"))
    assert set(data) == {"tasks"}
    assert len(data["tasks"]) == 1


def test_task_manager_tool_and_settings_create_tasks_without_ai_request(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        FlexEdgeOverlay,
        "_install_global_mouse_hook",
        lambda _self: None,
    )
    app = QApplication.instance() or QApplication([])
    window = FlexMainWindow(
        Settings(
            project_root=tmp_path,
            browser_enabled=False,
            terminal_enabled=False,
            memory_enabled=False,
            automation_enabled=False,
        )
    )

    try:
        assert "manage_tasks" in window.registry
        assert window._task_scheduler_timer.isActive()
        window.task_title_input.setText("Study")
        start = QDateTime.currentDateTime().addDays(1)
        window.task_start_input.setDateTime(start)
        window.task_end_input.setDateTime(start.addSecs(3_600))
        window._create_task_from_settings()
        assert len(window.task_manager.get_tasks()) == 1
        assert window.task_list.count() == 1
        assert window.task_manager_status.text() == "Task set."
        assert window.edge_overlay._task_notification_card is None
        assert window._request_worker is None
    finally:
        window.close()
        window.edge_overlay.close()
        app.processEvents()


def test_task_notifications_use_overlay_queue_and_can_be_dismissed(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        FlexEdgeOverlay,
        "_install_global_mouse_hook",
        lambda _self: None,
    )
    app = QApplication.instance() or QApplication([])
    overlay = FlexEdgeOverlay(Settings(project_root=tmp_path))
    task = {
        "id": "task_1",
        "title": "Study",
        "category": "study",
        "start": "2026-11-10T17:00:00+06:00",
        "end": "2026-11-10T20:00:00+06:00",
        "timezone": "Asia/Dhaka",
    }

    try:
        assert overlay.enqueue_task_notification(task, "started", 10_000)
        assert overlay.enqueue_task_notification(task, "ended", 10_000)
        assert overlay._task_notification_card is not None
        assert overlay._task_notification_card.state == "started"
        assert overlay._task_notification_card.width() == min(
            overlay.settings.bar_width,
            QApplication.primaryScreen().geometry().width(),
        )
        assert overlay.width() == overlay._task_notification_card.width() + 24
        assert overlay._task_notification_card.height() < 194
        assert not overlay._task_notification_card.grab().toImage().isNull()
        assert len(overlay._task_notification_queue) == 1
        assert overlay._task_notification_card._time_range() == "5:00 PM – 8:00 PM"
        animation = overlay._task_notification_animation
        assert animation is not None
        card_position = overlay.mapToGlobal(animation.endValue().topLeft())
        screen = QApplication.primaryScreen().geometry()
        assert (
            abs(
                card_position.x()
                + overlay._task_notification_card.width() // 2
                - screen.center().x()
            )
            <= 1
        )
        assert screen.top() <= card_position.y() <= screen.top() + 24
        assert animation.startValue().y() < animation.endValue().y()

        overlay._task_notification_card.dismissed.emit()
        app.processEvents()
        from PySide6.QtTest import QTest

        QTest.qWait(220)
        assert overlay._task_notification_card is not None
        assert overlay._task_notification_card.state == "ended"
        overlay._task_notification_card.dismissed.emit()
        QTest.qWait(220)
        assert overlay._task_notification_card is None
    finally:
        overlay.close()
        app.processEvents()
