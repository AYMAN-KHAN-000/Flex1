from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from typing import Any

from flex.automation.models import AutomationRequest, AutomationStep, WorkflowTrigger
from flex.automation.storage import AutomationStore, redact_sensitive
from flex.security.permissions import PermissionLevel, PermissionManager
from flex.security.redaction import redact_sensitive_text
from flex.security.validator import ArgumentValidator
from flex.tools.registry import ToolRegistry, ToolSpec

logger = logging.getLogger("flex.automation")


def _error(code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "success": False,
        "error": {"code": code, "message": message, "retryable": retryable},
    }


class AutomationRunner:
    def __init__(
        self,
        registry: ToolRegistry,
        permission_manager: PermissionManager,
        store: AutomationStore,
        *,
        enabled: bool = True,
        max_steps: int = 20,
        max_runtime_seconds: int = 300,
        max_retries: int = 2,
        default_tool_timeout_ms: int = 30_000,
    ) -> None:
        self.registry = registry
        self.permission_manager = permission_manager
        self.store = store
        self.enabled = enabled
        self.max_steps = max_steps
        self.max_runtime_seconds = max_runtime_seconds
        self.max_retries = max_retries
        self.default_tool_timeout_ms = default_tool_timeout_ms

    def run(
        self,
        name: str,
        description: str,
        steps: list[AutomationStep | dict[str, Any]],
        *,
        trigger: WorkflowTrigger | dict[str, Any] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            return _error("WORKFLOW_DISABLED", "Automations are disabled in FLEX settings.")
        try:
            request = AutomationRequest(
                name=name,
                description=description,
                trigger=trigger,
                steps=steps,
            )
        except ValueError as exc:
            return _error("WORKFLOW_INVALID", str(exc))
        if len(request.steps) > self.max_steps:
            return _error(
                "WORKFLOW_STEP_LIMIT",
                f"Automation has {len(request.steps)} steps; the configured limit is {self.max_steps}.",
            )

        prepared: list[tuple[AutomationStep, ToolSpec, dict[str, Any]]] = []
        for step in request.steps:
            if step.tool == "run_automation":
                return _error("WORKFLOW_INVALID", "An automation cannot recursively run another automation.")
            if step.tool not in self.registry:
                return _error("WORKFLOW_INVALID", f"Tool '{step.tool}' is not registered.")
            tool = self.registry.get(step.tool)
            if tool.argument_model is None:
                return _error("WORKFLOW_INVALID", f"Tool '{step.tool}' has no validated argument schema.")
            try:
                validated = ArgumentValidator.validate(tool.argument_model, step.arguments)
            except ValueError as exc:
                return _error("WORKFLOW_INVALID", f"Invalid arguments for {step.tool}: {exc}")
            prepared.append((step, tool, validated.model_dump()))

        workflow_id = self.store.create_workflow(
            request.name,
            request.description,
            [
                {
                    **step.model_dump(),
                    "permission": tool.permission_level.value,
                    "verification": "registered_tool",
                }
                for step, tool, _arguments in prepared
            ],
            request.trigger.model_dump() if request.trigger is not None else None,
        )
        high_risk = [
            (step, tool)
            for step, tool, _arguments in prepared
            if tool.permission_level == PermissionLevel.HIGH_RISK
        ]
        if high_risk:
            details = self._confirmation_summary(request.name, prepared)
            permission = self.permission_manager.can_execute(
                "run_automation",
                PermissionLevel.HIGH_RISK,
                details,
            )
            if not permission.allowed:
                return _error(
                    "CONFIRMATION_REQUIRED",
                    "The automation contains high-risk actions and was not approved.",
                )

        run_id = self.store.start_run(workflow_id)
        logger.info(
            "Automation started workflow_id=%s run_id=%s steps=%s",
            workflow_id,
            run_id,
            len(prepared),
        )
        started = time.monotonic()
        completed: list[dict[str, Any]] = []
        failed: dict[str, Any] | None = None
        status = "COMPLETED"
        failure_code: str | None = None
        remaining_start = len(prepared)

        for ordinal, (step, tool, arguments) in enumerate(prepared):
            if cancel_event is not None and cancel_event.is_set():
                status = "CANCELLED"
                failure_code = "WORKFLOW_CANCELLED"
                failed = _error(failure_code, "Automation was cancelled by the user.")
                remaining_start = ordinal
                break
            if time.monotonic() - started >= self.max_runtime_seconds:
                status = "FAILED"
                failure_code = "WORKFLOW_TIMEOUT"
                failed = _error(failure_code, "Automation exceeded its configured runtime.")
                remaining_start = ordinal
                break

            self.store.start_step(run_id, step.id, ordinal, tool.name)
            step_started = time.monotonic()
            logger.info(
                "Automation step started workflow_id=%s run_id=%s step_id=%s tool=%s",
                workflow_id,
                run_id,
                step.id,
                tool.name,
            )
            result: dict[str, Any]
            attempts = 0
            while True:
                timeout_owner = getattr(tool.executor, "__self__", None)
                previous_timeout = getattr(timeout_owner, "timeout_ms", None)
                if previous_timeout is not None:
                    remaining_ms = max(
                        1,
                        round(
                            (
                                self.max_runtime_seconds
                                - (time.monotonic() - started)
                            )
                            * 1000
                        ),
                    )
                    requested_timeout = step.timeout_ms or self.default_tool_timeout_ms
                    timeout_owner.timeout_ms = min(
                        previous_timeout,
                        requested_timeout,
                        remaining_ms,
                    )
                try:
                    result = tool.executor(**arguments)
                    if not isinstance(result, dict):
                        result = _error(
                            "WORKFLOW_STEP_FAILED",
                            f"Tool '{tool.name}' returned an invalid result.",
                        )
                except Exception as exc:
                    logger.error(
                        "Automation step raised workflow_id=%s run_id=%s step_id=%s",
                        workflow_id,
                        run_id,
                        step.id,
                    )
                    result = _error(
                        "WORKFLOW_STEP_FAILED",
                        redact_sensitive_text(str(exc)),
                    )
                finally:
                    if previous_timeout is not None:
                        timeout_owner.timeout_ms = previous_timeout
                success = result.get("success") is not False and "error" not in result
                if success:
                    try:
                        success = self._verify(tool, arguments, result)
                    except Exception as exc:
                        logger.error("Automation verification failed for tool=%s.", tool.name)
                        result = _error("WORKFLOW_VERIFICATION_FAILED", str(exc))
                        success = False
                error = result.get("error")
                retryable = isinstance(error, dict) and error.get("retryable") is True
                may_retry = (
                    not success
                    and retryable
                    and tool.permission_level == PermissionLevel.READ_ONLY
                    and attempts < self.max_retries
                    and (cancel_event is None or not cancel_event.is_set())
                    and time.monotonic() - started < self.max_runtime_seconds
                )
                if not may_retry:
                    break
                attempts += 1

            duration_ms = round((time.monotonic() - step_started) * 1000)
            code = error.get("code") if isinstance(error, dict) else None
            logger.info(
                "Automation step finished workflow_id=%s run_id=%s step_id=%s tool=%s success=%s duration_ms=%s error_code=%s",
                workflow_id,
                run_id,
                step.id,
                tool.name,
                success,
                duration_ms,
                code,
            )
            if success:
                self.store.finish_step(
                    step.id,
                    status="COMPLETED",
                    duration_ms=duration_ms,
                    result=result,
                )
                completed.append({"step_id": step.id, "tool": tool.name, "result": result})
                if cancel_event is not None and cancel_event.is_set():
                    status = "CANCELLED"
                    failure_code = "WORKFLOW_CANCELLED"
                    failed = _error(failure_code, "Automation was cancelled by the user.")
                    remaining_start = ordinal + 1
                    break
                if time.monotonic() - started >= self.max_runtime_seconds:
                    status = "FAILED"
                    failure_code = "WORKFLOW_TIMEOUT"
                    failed = _error(failure_code, "Automation exceeded its configured runtime.")
                    remaining_start = ordinal + 1
                    break
                continue

            self.store.finish_step(
                step.id,
                status="FAILED",
                duration_ms=duration_ms,
                error_code=code or "WORKFLOW_STEP_FAILED",
                result=result,
            )
            if step.continue_on_failure:
                if status == "COMPLETED":
                    status = "FAILED"
                    failure_code = code or "WORKFLOW_STEP_FAILED"
                    failed = {
                        "step_id": step.id,
                        "tool": tool.name,
                        "error": result.get(
                            "error",
                            {"code": failure_code, "message": "Step verification failed.", "retryable": False},
                        ),
                    }
                completed.append(
                    {
                        "step_id": step.id,
                        "tool": tool.name,
                        "success": False,
                        "error": result.get("error"),
                    }
                )
                continue
            failure_code = code or "WORKFLOW_STEP_FAILED"
            failed = {
                "step_id": step.id,
                "tool": tool.name,
                "error": result.get(
                    "error",
                    {"code": failure_code, "message": "Step verification failed.", "retryable": False},
                ),
            }
            status = (
                "CANCELLED"
                if failure_code == "WORKFLOW_CANCELLED"
                or (cancel_event is not None and cancel_event.is_set())
                else "FAILED"
            )
            if status == "CANCELLED":
                failure_code = "WORKFLOW_CANCELLED"
            remaining_start = ordinal + 1
            break

        if status != "COMPLETED":
            for index in range(remaining_start, len(prepared)):
                step, _tool, _arguments = prepared[index]
                self.store.skip_step(run_id, {"id": step.id, "tool": step.tool}, index)
        self.store.finish_run(run_id, status, failure_code)
        return {
            "success": status == "COMPLETED",
            "workflow_id": workflow_id,
            "run_id": run_id,
            "status": status,
            "completed": completed,
            "failed": failed,
            "remaining": [
                {"step_id": step.id, "tool": step.tool}
                for step, _tool, _arguments in prepared[remaining_start:]
            ] if status != "COMPLETED" else [],
            "error": (
                {
                    "code": failure_code,
                    "message": (
                        "Automation was cancelled." if status == "CANCELLED"
                        else "Automation exceeded its configured runtime." if failure_code == "WORKFLOW_TIMEOUT"
                        else "Automation stopped after a failed step."
                    ),
                    "retryable": status == "FAILED",
                }
                if status != "COMPLETED"
                else None
            ),
        }

    @staticmethod
    def _verify(tool: ToolSpec, arguments: dict[str, Any], result: dict[str, Any]) -> bool:
        if result.get("success") is False or "error" in result:
            return False
        if tool.verification is None:
            return bool(result)
        return tool.verification(arguments, result)

    @staticmethod
    def _confirmation_summary(
        name: str,
        prepared: list[tuple[AutomationStep, ToolSpec, dict[str, Any]]],
    ) -> str:
        steps = []
        for index, (step, tool, arguments) in enumerate(prepared, start=1):
            safe_arguments = redact_sensitive(arguments)
            if step.tool in {"type_text", "browser_type"} and isinstance(safe_arguments, dict):
                safe_arguments["text"] = "[REDACTED]"
            steps.append(
                {
                    "step": index,
                    "tool": tool.name,
                    "permission": tool.permission_level.value,
                    "arguments": safe_arguments,
                }
            )
        return json.dumps({"automation": name, "steps": steps}, ensure_ascii=False, default=str)


def create_automation_tool(
    runner: AutomationRunner,
    cancel_event: threading.Event,
) -> ToolSpec:
    def run_automation(
        name: str,
        description: str = "",
        steps: list[AutomationStep] | None = None,
        trigger: WorkflowTrigger | None = None,
    ) -> dict[str, Any]:
        try:
            return runner.run(
                name,
                description,
                steps or [],
                trigger=trigger,
                cancel_event=cancel_event,
            )
        except sqlite3.Error:
            logger.error("Automation database operation failed.")
            return _error("AUTOMATION_DATABASE_ERROR", "SQLite could not save the automation run.")
        except Exception as exc:
            logger.error("Automation failed unexpectedly error_type=%s", type(exc).__name__)
            return _error("WORKFLOW_FAILED", "Automation failed unexpectedly.")

    return ToolSpec(
        name="run_automation",
        description=(
            "Execute a short, validated sequence of registered FLEX tools. Stop on failure by default. "
            "High-risk operations require approval of the complete action summary."
        ),
        schema=AutomationRequest.model_json_schema(),
        permission_level=PermissionLevel.LOW_RISK,
        executor=run_automation,
        argument_model=AutomationRequest,
        verification=lambda _arguments, result: result.get("success") is True,
        timeout=runner.max_runtime_seconds,
    )
