from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

_SENSITIVE_KEY = re.compile(
    r"password|passphrase|api[_-]?key|secret|token|cookie|authorization|"
    r"clipboard|^text$|^content$|^value$",
    re.IGNORECASE,
)


def redact_sensitive(value: Any, key: str = "") -> Any:
    if _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if key.casefold() == "arguments" and isinstance(value, list):
        return ["[REDACTED]" for _ in value]
    if isinstance(value, dict):
        return {str(child_key): redact_sensitive(child, str(child_key)) for child_key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(child) for child in value]
    if isinstance(value, str):
        if re.search(
            r"\bBearer\s+\S+|sk-or-v1-[A-Za-z0-9_-]+|\bgh[pousr]_[A-Za-z0-9]{20,}\b|\bAKIA[0-9A-Z]{16}\b",
            value,
            re.IGNORECASE,
        ):
            return "[REDACTED]"
        if len(value) > 1_000:
            return value[:1_000] + "[TRUNCATED]"
    return value


class AutomationStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS automation_workflows (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    trigger_json TEXT NOT NULL DEFAULT '{}',
                    steps_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS automation_runs (
                    id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL REFERENCES automation_workflows(id),
                    status TEXT NOT NULL CHECK (
                        status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED')
                    ),
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    error_code TEXT
                );
                CREATE TABLE IF NOT EXISTS automation_steps (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES automation_runs(id) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL,
                    tool TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'SKIPPED')
                    ),
                    started_at TEXT,
                    ended_at TEXT,
                    duration_ms INTEGER,
                    error_code TEXT,
                    result_summary TEXT NOT NULL DEFAULT '{}',
                    UNIQUE (run_id, ordinal)
                );
                CREATE INDEX IF NOT EXISTS automation_runs_started
                    ON automation_runs(started_at DESC);
                """
            )
            workflow_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(automation_workflows)")
            }
            if "trigger_json" not in workflow_columns:
                connection.execute(
                    "ALTER TABLE automation_workflows "
                    "ADD COLUMN trigger_json TEXT NOT NULL DEFAULT '{}'"
                )

    def create_workflow(
        self,
        name: str,
        description: str,
        steps: list[dict[str, Any]],
        trigger: dict[str, Any] | None = None,
    ) -> str:
        workflow_id = uuid4().hex
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        safe_steps = redact_sensitive(steps)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO automation_workflows
                    (id, name, description, trigger_json, steps_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workflow_id,
                    name.strip(),
                    description.strip(),
                    json.dumps(trigger, ensure_ascii=False, default=str) if trigger else "{}",
                    json.dumps(safe_steps, ensure_ascii=False, default=str),
                    now,
                    now,
                ),
            )
        return workflow_id

    def start_run(self, workflow_id: str) -> str:
        run_id = uuid4().hex
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO automation_runs (id, workflow_id, status, started_at)
                VALUES (?, ?, 'RUNNING', ?)
                """,
                (run_id, workflow_id, now),
            )
        return run_id

    def start_step(self, run_id: str, step_id: str, ordinal: int, tool: str) -> None:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO automation_steps
                    (id, run_id, ordinal, tool, status, started_at)
                VALUES (?, ?, ?, ?, 'RUNNING', ?)
                """,
                (step_id, run_id, ordinal, tool, now),
            )

    def finish_step(
        self,
        step_id: str,
        *,
        status: str,
        duration_ms: int,
        error_code: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        summary = {}
        if result:
            error = result.get("error")
            summary = {
                "success": result.get("success"),
                "error_code": error.get("code") if isinstance(error, dict) else None,
            }
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE automation_steps
                SET status = ?, ended_at = ?, duration_ms = ?, error_code = ?,
                    result_summary = ?
                WHERE id = ?
                """,
                (
                    status,
                    now,
                    duration_ms,
                    error_code,
                    json.dumps(summary),
                    step_id,
                ),
            )

    def finish_run(self, run_id: str, status: str, error_code: str | None = None) -> None:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE automation_runs
                SET status = ?, ended_at = ?, error_code = ?
                WHERE id = ?
                """,
                (status, now, error_code, run_id),
            )

    def skip_step(self, run_id: str, step: dict[str, Any], ordinal: int) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO automation_steps (id, run_id, ordinal, tool, status)
                VALUES (?, ?, ?, ?, 'SKIPPED')
                """,
                (step["id"], run_id, ordinal, step["tool"]),
            )
