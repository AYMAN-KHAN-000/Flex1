from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


class ResearchHistory:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS research_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request TEXT NOT NULL,
                    category TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    selected_result TEXT,
                    sources TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    opened INTEGER NOT NULL DEFAULT 0
                )
                """
            )

    def save(
        self,
        request: str,
        category: str,
        sources: list[dict[str, Any]],
        summary: str,
        *,
        selected_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            cursor = conn.execute(
                """
                INSERT INTO research_history
                    (request, category, created_at, selected_result, sources, summary, opened)
                VALUES (?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    request[:1000],
                    category,
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                    json.dumps(selected_result, ensure_ascii=False)
                    if selected_result
                    else None,
                    json.dumps(sources, ensure_ascii=False),
                    summary[:2000],
                ),
            )
            research_id = cursor.lastrowid
        return self.get(research_id)

    def get(self, research_id: int) -> dict[str, Any]:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            row = conn.execute(
                "SELECT id, request, category, created_at, selected_result, sources, summary, opened "
                "FROM research_history WHERE id = ?",
                (research_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"Research record {research_id} was not found.")
        return self._item(row)

    @staticmethod
    def _item(row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "id": row[0],
            "request": row[1],
            "category": row[2],
            "created_at": row[3],
            "selected_result": json.loads(row[4]) if row[4] else None,
            "sources": json.loads(row[5]),
            "summary": row[6],
            "opened": bool(row[7]),
        }

    def mark_opened(self, research_id: int) -> None:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            cursor = conn.execute(
                "UPDATE research_history SET opened = 1 WHERE id = ?",
                (research_id,),
            )
        if cursor.rowcount == 0:
            raise KeyError(f"Research record {research_id} was not found.")

    def list(self, limit: int = 10) -> list[dict[str, Any]]:
        if not 1 <= limit <= 50:
            raise ValueError("Research history limit must be between 1 and 50.")
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            rows = conn.execute(
                "SELECT id, request, category, created_at, selected_result, sources, summary, opened "
                "FROM research_history ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._item(row) for row in rows]

    def clear(self) -> int:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            cursor = conn.execute("DELETE FROM research_history")
        return cursor.rowcount
