from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from flex.security.permissions import PermissionLevel
from flex.tasks.task_manager import TaskManager
from flex.tools.registry import ToolSpec


class TaskManagerArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "create", "list", "edit", "pause", "resume", "cancel", "delete",
        "set_notifications",
    ]
    task_id: str | None = None
    title: str | None = None
    start: str | None = Field(
        default=None,
        description="ISO date/time, preferably with the task's IANA timezone offset.",
    )
    end: str | None = Field(default=None, description="Optional ISO date/time.")
    timezone: str | None = Field(default=None, description="IANA timezone, e.g. Asia/Dhaka.")
    category: str | None = None
    notify_start: bool | None = None
    notify_end: bool | None = None


def _task_manager_verifier(
    _arguments: dict[str, Any],
    result: dict[str, Any],
) -> bool:
    return result.get("success") is True


def create_task_manager_tool(manager: TaskManager) -> ToolSpec:
    def execute(
        action: str,
        task_id: str | None = None,
        title: str | None = None,
        start: str | None = None,
        end: str | None = None,
        timezone: str | None = None,
        category: str | None = None,
        notify_start: bool | None = None,
        notify_end: bool | None = None,
    ) -> dict[str, Any]:
        if action == "create":
            if not title or not start:
                raise ValueError("Creating a task requires a title and start date/time.")
            return manager.create_task(
                title,
                start,
                end,
                timezone=timezone,
                category=category,
                notify_start=True if notify_start is None else notify_start,
                notify_end=True if notify_end is None else notify_end,
            )
        if action == "list":
            return {"success": True, "tasks": manager.get_tasks()}
        if action == "set_notifications":
            if not task_id or (notify_start is None and notify_end is None):
                raise ValueError("Choose a task and provide its notification setting.")
            updates = {}
            if notify_start is not None:
                updates["notify_start"] = notify_start
            if notify_end is not None:
                updates["notify_end"] = notify_end
            return manager.edit_task(task_id, **updates)
        if not task_id:
            raise ValueError(f"The {action} action requires a task ID.")
        if action == "edit":
            updates = {
                name: value
                for name, value in {
                    "title": title,
                    "start": start,
                    "end": end,
                    "timezone": timezone,
                    "category": category,
                    "notify_start": notify_start,
                    "notify_end": notify_end,
                }.items()
                if value is not None
            }
            return manager.edit_task(task_id, **updates)
        if action == "pause":
            return manager.pause_task(task_id)
        if action == "resume":
            return manager.resume_task(task_id)
        if action == "cancel":
            return manager.cancel_task(task_id)
        if action == "delete":
            return manager.delete_task(task_id)
        raise ValueError(f"Unsupported task action: {action}")

    return ToolSpec(
        name="manage_tasks",
        description=(
            "Create, list, edit, pause, resume, cancel, or delete scheduled tasks. "
            "Before creating, use the user's local date/time and their configured IANA timezone; "
            "ask a clarifying question if the title or scheduling time is missing or ambiguous. "
            "Use ISO datetimes. For reminder-only tasks omit end. Always list tasks before choosing "
            "a task ID to modify. Report success only when the tool returns success."
        ),
        schema=TaskManagerArgs.model_json_schema(),
        permission_level=PermissionLevel.LOW_RISK,
        executor=execute,
        argument_model=TaskManagerArgs,
        verification=_task_manager_verifier,
    )
