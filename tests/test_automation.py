from __future__ import annotations

import sqlite3
import threading
import time

from pydantic import BaseModel, ConfigDict

from flex.automation import AutomationRunner
from flex.automation.models import AutomationStep
from flex.automation.storage import AutomationStore
from flex.security.permissions import PermissionLevel, PermissionManager
from flex.tools.registry import ToolRegistry, ToolSpec


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


def _tool(name, executor, permission=PermissionLevel.READ_ONLY):
    return ToolSpec(
        name=name,
        description=name,
        schema=Args.model_json_schema(),
        permission_level=permission,
        executor=executor,
        argument_model=Args,
        verification=lambda _arguments, result: result.get("success") is True,
    )


def _runner(registry, path, **kwargs):
    return AutomationRunner(
        registry,
        kwargs.pop("permission_manager", PermissionManager(require_confirmation=False)),
        AutomationStore(path),
        **kwargs,
    )


def test_one_step_and_multi_step_automation_complete_and_persist(tmp_path):
    registry = ToolRegistry()
    calls = []
    registry.register(_tool("first", lambda value: calls.append(value) or {"success": True}))
    registry.register(_tool("second", lambda value: calls.append(value) or {"success": True}))
    runner = _runner(registry, tmp_path / "flex.db")

    result = runner.run(
        "two steps",
        "test sequence",
        [
            AutomationStep(tool="first", arguments={"value": "one"}),
            AutomationStep(tool="second", arguments={"value": "two"}),
        ],
    )

    assert result["success"] is True
    assert result["status"] == "COMPLETED"
    assert calls == ["one", "two"]
    with sqlite3.connect(tmp_path / "flex.db") as connection:
        run_status = connection.execute("SELECT status FROM automation_runs").fetchone()[0]
        step_count = connection.execute("SELECT COUNT(*) FROM automation_steps").fetchone()[0]
    assert run_status == "COMPLETED"
    assert step_count == 2


def test_failed_step_stops_and_marks_remaining_steps_skipped(tmp_path):
    registry = ToolRegistry()
    calls = []
    registry.register(_tool("first", lambda value: {"success": True}))
    registry.register(
        _tool(
            "fail",
            lambda value: {
                "success": False,
                "error": {"code": "FAILED", "message": "step failed", "retryable": False},
            },
        )
    )
    registry.register(_tool("never", lambda value: calls.append(value) or {"success": True}))

    result = _runner(registry, tmp_path / "flex.db").run(
        "stop on error",
        "",
        [
            AutomationStep(tool="first", arguments={"value": "1"}),
            AutomationStep(tool="fail", arguments={"value": "2"}),
            AutomationStep(tool="never", arguments={"value": "3"}),
        ],
    )

    assert result["success"] is False
    assert result["status"] == "FAILED"
    assert result["failed"]["tool"] == "fail"
    assert result["remaining"][0]["tool"] == "never"
    assert calls == []
    with sqlite3.connect(tmp_path / "flex.db") as connection:
        statuses = [
            row[0]
            for row in connection.execute(
                "SELECT status FROM automation_steps ORDER BY ordinal"
            )
        ]
    assert statuses == ["COMPLETED", "FAILED", "SKIPPED"]


def test_read_only_retry_is_limited_and_succeeds(tmp_path):
    registry = ToolRegistry()
    calls = 0

    def sometimes_fails(value):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "success": False,
                "error": {"code": "TEMPORARY", "message": "retry", "retryable": True},
            }
        return {"success": True}

    registry.register(_tool("read", sometimes_fails))
    result = _runner(registry, tmp_path / "flex.db", max_retries=1).run(
        "retry read",
        "",
        [AutomationStep(tool="read", arguments={"value": "x"})],
    )
    assert result["success"] is True
    assert calls == 2


def test_cancelled_workflow_runs_no_steps_and_records_status(tmp_path):
    registry = ToolRegistry()
    calls = []
    registry.register(_tool("read", lambda value: calls.append(value) or {"success": True}))
    cancelled = threading.Event()
    cancelled.set()

    result = _runner(registry, tmp_path / "flex.db").run(
        "cancel",
        "",
        [AutomationStep(tool="read", arguments={"value": "x"})],
        cancel_event=cancelled,
    )
    assert result["status"] == "CANCELLED"
    assert result["error"]["code"] == "WORKFLOW_CANCELLED"
    assert calls == []
    with sqlite3.connect(tmp_path / "flex.db") as connection:
        assert connection.execute("SELECT status FROM automation_runs").fetchone()[0] == "CANCELLED"


def test_cancellation_between_steps_preserves_completed_and_remaining(tmp_path):
    registry = ToolRegistry()
    cancelled = threading.Event()

    def stop_after_first(value):
        cancelled.set()
        return {"success": True}

    registry.register(_tool("first", stop_after_first))
    registry.register(_tool("second", lambda value: {"success": True}))
    result = _runner(registry, tmp_path / "flex.db").run(
        "cancel after first",
        "",
        [
            AutomationStep(tool="first", arguments={"value": "1"}),
            AutomationStep(tool="second", arguments={"value": "2"}),
        ],
        cancel_event=cancelled,
    )

    assert result["status"] == "CANCELLED"
    assert result["completed"][0]["tool"] == "first"
    assert result["remaining"][0]["tool"] == "second"


def test_runtime_limit_stops_later_steps(tmp_path):
    registry = ToolRegistry()
    calls = []

    def slow(value):
        time.sleep(1.1)
        return {"success": True}

    registry.register(_tool("slow", slow))
    registry.register(_tool("never", lambda value: calls.append(value) or {"success": True}))
    result = _runner(
        registry,
        tmp_path / "flex.db",
        max_runtime_seconds=1,
    ).run(
        "runtime limit",
        "",
        [
            AutomationStep(tool="slow", arguments={"value": "x"}),
            AutomationStep(tool="never", arguments={"value": "y"}),
        ],
    )
    assert result["status"] == "FAILED"
    assert result["error"]["code"] == "WORKFLOW_TIMEOUT"
    assert calls == []


def test_high_risk_automation_requests_one_summary_confirmation(tmp_path):
    registry = ToolRegistry()
    calls = []
    registry.register(
        _tool(
            "dangerous",
            lambda value: calls.append(value) or {"success": True},
            PermissionLevel.HIGH_RISK,
        )
    )
    prompts = []
    permissions = PermissionManager(
        confirmation_callback=lambda name, details: prompts.append((name, details)) or False
    )
    result = _runner(
        registry,
        tmp_path / "flex.db",
        permission_manager=permissions,
    ).run(
        "important sequence",
        "",
        [AutomationStep(tool="dangerous", arguments={"value": "safe display"})],
    )
    assert result["error"]["code"] == "CONFIRMATION_REQUIRED"
    assert calls == []
    assert len(prompts) == 1
    assert "important sequence" in prompts[0][1]


def test_persisted_workflow_records_trigger_and_redacts_free_text(tmp_path):
    registry = ToolRegistry()
    registry.register(_tool("read", lambda value: {"success": True}))
    result = _runner(registry, tmp_path / "flex.db").run(
        "remembered flow",
        "",
        [AutomationStep(tool="read", arguments={"value": "sensitive text"})],
        trigger={"type": "phrase", "value": "start coding"},
    )

    with sqlite3.connect(tmp_path / "flex.db") as connection:
        trigger, steps = connection.execute(
            "SELECT trigger_json, steps_json FROM automation_workflows WHERE id = ?",
            (result["workflow_id"],),
        ).fetchone()
    assert '"start coding"' in trigger
    assert "sensitive text" not in steps
    assert "[REDACTED]" in steps


def test_step_argument_validation_and_limits_prevent_execution(tmp_path):
    registry = ToolRegistry()
    calls = []
    registry.register(_tool("read", lambda value: calls.append(value) or {"success": True}))
    runner = _runner(registry, tmp_path / "flex.db", max_steps=1)

    too_many = runner.run(
        "too many",
        "",
        [
            AutomationStep(tool="read", arguments={"value": "1"}),
            AutomationStep(tool="read", arguments={"value": "2"}),
        ],
    )
    invalid = runner.run(
        "invalid",
        "",
        [AutomationStep(tool="read", arguments={"unexpected": "x"})],
    )

    assert too_many["error"]["code"] == "WORKFLOW_STEP_LIMIT"
    assert invalid["error"]["code"] == "WORKFLOW_INVALID"
    assert calls == []
