from __future__ import annotations

import json
import logging
import os
import tempfile
from functools import wraps
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)
MISSED_END_NOTIFICATION_WINDOW = timedelta(hours=12)
MISSED_REMINDER_WINDOW = timedelta(minutes=10)


def _synchronized(method: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(method)
    def wrapped(self: TaskManager, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapped


class TaskStorageError(RuntimeError):
    """Raised when persisted task data cannot be read or safely written."""


class TaskManager:
    def __init__(
        self,
        path: str | Path,
        *,
        default_timezone: str = "Asia/Dhaka",
        auto_cleanup: bool = True,
    ) -> None:
        self.path = Path(path)
        self._lock = RLock()
        self.default_timezone = self._validated_timezone(default_timezone)
        self.auto_cleanup = auto_cleanup

    def set_default_timezone(self, timezone: str) -> None:
        self.default_timezone = self._validated_timezone(timezone)

    @staticmethod
    def _validated_timezone(value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, TypeError) as exc:
            raise ValueError(f"Unknown IANA timezone: {value}") from exc
        return value

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            tasks = payload["tasks"]
            if not isinstance(tasks, list):
                raise ValueError("The tasks field must be a list.")
            for task in tasks:
                self._validate_record(task)
            task_ids = [task["id"] for task in tasks]
            if len(task_ids) != len(set(task_ids)):
                raise ValueError("Task IDs must be unique.")
            return tasks
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise TaskStorageError(
                f"Could not read task storage at '{self.path}': {exc}"
            ) from exc

    @staticmethod
    def _validate_record(task: Any) -> None:
        required = {
            "id", "title", "category", "start", "end", "timezone", "status",
            "notify_start", "notify_end", "start_notified", "end_notified",
        }
        if not isinstance(task, dict) or not required.issubset(task):
            raise ValueError("A task record is missing required fields.")
        if not isinstance(task["title"], str) or not task["title"].strip():
            raise ValueError("Task titles cannot be empty.")
        if task["status"] not in {
            "scheduled", "active", "paused", "completed", "cancelled",
        }:
            raise ValueError("A task has an invalid status.")
        start = datetime.fromisoformat(task["start"])
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError("Task start times must include a timezone.")
        if task["end"] is not None:
            end = datetime.fromisoformat(task["end"])
            if (
                end.tzinfo is None
                or end.utcoffset() is None
                or end.astimezone(ZoneInfo("UTC"))
                <= start.astimezone(ZoneInfo("UTC"))
            ):
                raise ValueError("Task end must be timezone-aware and after its start.")
        if not isinstance(task["id"], str) or not task["id"]:
            raise ValueError("Task IDs cannot be empty.")
        if not isinstance(task["category"], str):
            raise ValueError("Task category must be text.")
        for flag in (
            "notify_start", "notify_end", "start_notified", "end_notified",
        ):
            if not isinstance(task[flag], bool):
                raise ValueError(f"Task field '{flag}' must be boolean.")
        TaskManager._validated_timezone(task["timezone"])

    def _write(self, tasks: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as file:
                temp_path = Path(file.name)
                json.dump({"tasks": tasks}, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, self.path)
        except OSError as exc:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    logger.exception("Could not remove temporary task storage file.")
            raise TaskStorageError(
                f"Could not save task storage at '{self.path}': {exc}"
            ) from exc

    def _parse_datetime(self, value: str, timezone: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Use an ISO date/time such as 2026-11-10T17:00:00."
            ) from exc
        zone = ZoneInfo(timezone)
        if parsed.tzinfo is None:
            first_fold = parsed.replace(tzinfo=zone, fold=0)
            second_fold = parsed.replace(tzinfo=zone, fold=1)
            if first_fold.astimezone(ZoneInfo("UTC")).astimezone(zone).replace(
                tzinfo=None
            ) != parsed:
                raise ValueError(
                    "This local time does not exist because of a daylight-saving change."
                )
            if first_fold.utcoffset() != second_fold.utcoffset():
                raise ValueError(
                    "This local time is ambiguous because of a daylight-saving change. "
                    "Provide an explicit UTC offset."
                )
            parsed = first_fold
        return parsed

    @_synchronized
    def get_tasks(self) -> list[dict[str, Any]]:
        return sorted(
            self._read(),
            key=lambda task: datetime.fromisoformat(task["start"]).astimezone(
                ZoneInfo("UTC")
            ),
        )

    @_synchronized
    def create_task(
        self,
        title: str,
        start: str,
        end: str | None = None,
        *,
        timezone: str | None = None,
        category: str | None = None,
        notify_start: bool = True,
        notify_end: bool = True,
    ) -> dict[str, Any]:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("Task title cannot be empty.")
        zone_name = self._validated_timezone(timezone or self.default_timezone)
        start_at = self._parse_datetime(start, zone_name)
        end_at = self._parse_datetime(end, zone_name) if end else None
        if (
            end_at is not None
            and end_at.astimezone(ZoneInfo("UTC"))
            <= start_at.astimezone(ZoneInfo("UTC"))
        ):
            raise ValueError("Task end time must be after its start time.")
        tasks = self._read()
        for existing in tasks:
            if (
                existing["title"].casefold() == clean_title.casefold()
                and datetime.fromisoformat(existing["start"]).astimezone(
                    ZoneInfo("UTC")
                )
                == start_at.astimezone(ZoneInfo("UTC"))
                and existing["status"] not in {"cancelled", "completed"}
            ):
                return {"success": True, "duplicate": True, "task": existing}
        warnings = [
            f"Overlaps with '{existing['title']}'."
            for existing in tasks
            if existing["status"] not in {"cancelled", "completed"}
            and end_at is not None
            and existing["end"] is not None
            and start_at.astimezone(ZoneInfo("UTC"))
            < datetime.fromisoformat(existing["end"]).astimezone(ZoneInfo("UTC"))
            and end_at.astimezone(ZoneInfo("UTC"))
            > datetime.fromisoformat(existing["start"]).astimezone(ZoneInfo("UTC"))
        ]
        task = {
            "id": f"task_{uuid4().hex}",
            "title": clean_title,
            "category": (category or clean_title).strip().lower(),
            "start": start_at.isoformat(),
            "end": end_at.isoformat() if end_at else None,
            "timezone": zone_name,
            "status": "scheduled",
            "notify_start": bool(notify_start),
            "notify_end": bool(notify_end and end_at is not None),
            "start_notified": False,
            "end_notified": False,
        }
        tasks.append(task)
        self._write(tasks)
        return {
            "success": True,
            "duplicate": False,
            "task": task,
            "warnings": warnings,
        }

    @_synchronized
    def edit_task(self, task_id: str, **updates: Any) -> dict[str, Any]:
        allowed = {"title", "start", "end", "timezone", "category", "notify_start", "notify_end"}
        if not updates or updates.keys() - allowed:
            raise ValueError("Provide valid task fields to update.")
        tasks = self._read()
        task = self._find(tasks, task_id)
        notification_updates = set(updates) & {"notify_start", "notify_end"}
        if task["status"] in {"completed", "cancelled"}:
            extra_updates = set(updates) - notification_updates
            if extra_updates:
                raise ValueError(
                    "Completed or cancelled tasks can only update their notification settings."
                )
            task["notify_start"] = bool(updates.get("notify_start", task["notify_start"]))
            task["notify_end"] = bool(
                updates.get("notify_end", task["notify_end"]) and task.get("end") is not None
            )
            self._write(tasks)
            return {"success": True, "task": task}
        timezone = self._validated_timezone(updates.get("timezone", task["timezone"]))
        title = updates.get("title", task["title"])
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Task title cannot be empty.")
        start_value = updates.get("start", task["start"])
        end_value = updates.get("end", task["end"])
        start_at = self._parse_datetime(start_value, timezone)
        end_at = self._parse_datetime(end_value, timezone) if end_value else None
        if (
            end_at is not None
            and end_at.astimezone(ZoneInfo("UTC"))
            <= start_at.astimezone(ZoneInfo("UTC"))
        ):
            raise ValueError("Task end time must be after its start time.")
        start_value_normalized = start_at.isoformat()
        end_value_normalized = end_at.isoformat() if end_at else None
        start_changed = (
            task["timezone"] != timezone or task["start"] != start_value_normalized
        )
        end_changed = (
            task["timezone"] != timezone or task["end"] != end_value_normalized
        )
        old_status = task["status"]
        start_notified = task["start_notified"]
        end_notified = task["end_notified"]
        task.update(
            {
                "title": title.strip(),
                "start": start_value_normalized,
                "end": end_value_normalized,
                "timezone": timezone,
                "category": str(updates.get("category", task["category"])).strip().lower(),
                "notify_start": bool(updates.get("notify_start", task["notify_start"])),
                "notify_end": bool(updates.get("notify_end", task["notify_end"]) and end_at),
                "start_notified": False if start_changed else start_notified,
                "end_notified": False if end_changed else end_notified,
                "status": (
                    "scheduled"
                    if start_changed and old_status in {"scheduled", "active"}
                    else old_status
                ),
            }
        )
        self._write(tasks)
        return {"success": True, "task": task}

    @_synchronized
    def pause_task(self, task_id: str) -> dict[str, Any]:
        return self._change_status(task_id, "paused", {"scheduled", "active"})

    @_synchronized
    def resume_task(self, task_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        tasks = self._read()
        task = self._find(tasks, task_id)
        if task["status"] != "paused":
            raise ValueError("Only paused tasks can be resumed.")
        current = now or datetime.now(ZoneInfo(task["timezone"]))
        end = datetime.fromisoformat(task["end"]) if task["end"] else None
        start = datetime.fromisoformat(task["start"])
        current_utc = current.astimezone(ZoneInfo("UTC"))
        if end is not None and end.astimezone(ZoneInfo("UTC")) <= current_utc:
            task["status"] = "completed"
        elif start.astimezone(ZoneInfo("UTC")) <= current_utc:
            task["status"] = "active"
        else:
            task["status"] = "scheduled"
        self._write(tasks)
        return {"success": True, "task": task}

    @_synchronized
    def cancel_task(self, task_id: str) -> dict[str, Any]:
        return self._change_status(
            task_id,
            "cancelled",
            {"scheduled", "active", "paused", "completed"},
        )

    @_synchronized
    def delete_task(self, task_id: str) -> dict[str, Any]:
        tasks = self._read()
        task = self._find(tasks, task_id)
        tasks.remove(task)
        self._write(tasks)
        return {"success": True, "deleted_id": task_id}

    def _change_status(
        self,
        task_id: str,
        status: str,
        valid_statuses: set[str],
    ) -> dict[str, Any]:
        tasks = self._read()
        task = self._find(tasks, task_id)
        if task["status"] not in valid_statuses:
            raise ValueError(f"Cannot set a {task['status']} task to {status}.")
        task["status"] = status
        self._write(tasks)
        return {"success": True, "task": task}

    @staticmethod
    def _find(tasks: list[dict[str, Any]], task_id: str) -> dict[str, Any]:
        for task in tasks:
            if task["id"] == task_id:
                return task
        raise KeyError(f"No task with ID '{task_id}' exists.")

    @_synchronized
    def process_due_tasks(
        self,
        enqueue_notification: Callable[[dict[str, Any], str], bool],
        *,
        now: datetime | None = None,
        notifications_enabled: bool = True,
        start_notifications_enabled: bool = True,
        end_notifications_enabled: bool = True,
        auto_cleanup: bool | None = None,
    ) -> list[dict[str, Any]]:
        tasks = self._read()
        changed = False
        events: list[dict[str, Any]] = []
        current_utc = (now or datetime.now().astimezone()).astimezone(ZoneInfo("UTC"))
        cleanup = self.auto_cleanup if auto_cleanup is None else auto_cleanup
        kept: list[dict[str, Any]] = []
        for task in tasks:
            if task["status"] in {"cancelled", "completed"}:
                if not (cleanup and task["status"] == "completed"):
                    kept.append(task)
                else:
                    changed = True
                continue
            start = datetime.fromisoformat(task["start"]).astimezone(ZoneInfo("UTC"))
            end = datetime.fromisoformat(task["end"]).astimezone(ZoneInfo("UTC")) if task["end"] else None
            if task["status"] == "paused":
                kept.append(task)
                continue
            if current_utc >= start and task["status"] == "scheduled":
                task["status"] = "active"
                changed = True
            if current_utc >= start and not task["start_notified"]:
                is_fresh = current_utc - start <= MISSED_REMINDER_WINDOW
                still_running = end is None or current_utc < end
                should_notify = (
                    task["notify_start"]
                    and notifications_enabled
                    and start_notifications_enabled
                    and (still_running or (end is None and is_fresh))
                )
                if should_notify:
                    if not enqueue_notification(task.copy(), "started"):
                        kept.append(task)
                        continue
                    events.append({"task_id": task["id"], "type": "started"})
                task["start_notified"] = True
                if end is None:
                    task["status"] = "completed"
                    changed = True
                    if cleanup:
                        changed = True
                        continue
                changed = True
            if end is not None and current_utc >= end:
                if not task["end_notified"]:
                    if (
                        task["notify_end"]
                        and notifications_enabled
                        and end_notifications_enabled
                        and current_utc - end <= MISSED_END_NOTIFICATION_WINDOW
                    ):
                        if not enqueue_notification(task.copy(), "ended"):
                            kept.append(task)
                            continue
                        events.append({"task_id": task["id"], "type": "ended"})
                    task["end_notified"] = True
                    changed = True
                task["status"] = "completed"
                changed = True
                if not cleanup:
                    kept.append(task)
            else:
                kept.append(task)
        if changed:
            self._write(kept)
        return events
