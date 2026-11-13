from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

MEMORY_CATEGORIES = (
    "preference",
    "fact",
    "project",
    "shortcut",
    "decision",
    "workflow_context",
)
_SECRET_LABEL = re.compile(
    r"\b(password|passphrase|api[_ -]?key|secret|token|authorization|cookie)\b",
    re.IGNORECASE,
)
_SECRET_VALUE = re.compile(
    r"\bBearer\s+\S+|sk-or-v1-[A-Za-z0-9_-]+|\bgh[pousr]_[A-Za-z0-9]{20,}\b|"
    r"\bAKIA[0-9A-Z]{16}\b",
    re.IGNORECASE,
)


class MemoryDatabase:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _initialize(self) -> None:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL CHECK (
                        category IN (
                            'preference', 'fact', 'project', 'shortcut',
                            'decision', 'workflow_context'
                        )
                    ),
                    key TEXT NOT NULL CHECK (length(trim(key)) BETWEEN 1 AND 200),
                    value TEXT NOT NULL CHECK (length(trim(value)) BETWEEN 1 AND 4000),
                    source TEXT NOT NULL DEFAULT 'explicit_user',
                    confidence REAL NOT NULL DEFAULT 1.0
                        CHECK (confidence >= 0.0 AND confidence <= 1.0),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (category, key COLLATE NOCASE)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS memories_category_updated "
                "ON memories(category, updated_at DESC)"
            )

    def add_message(self, role: str, content: str) -> None:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute(
                "INSERT INTO conversation (role, content) VALUES (?, ?)",
                (role, content),
            )

    def get_recent_messages(self, limit: int = 10) -> list[tuple[str, str]]:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            rows = conn.execute(
                "SELECT role, content FROM conversation ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [(role, content) for role, content in reversed(rows)]

    @staticmethod
    def _validate_memory(category: str, key: str, value: str) -> tuple[str, str, str]:
        normalized_category = category.strip().lower()
        normalized_key = key.strip()
        normalized_value = value.strip()
        if normalized_category not in MEMORY_CATEGORIES:
            raise ValueError(f"Unsupported memory category: {category!r}.")
        if not normalized_key or len(normalized_key) > 200:
            raise ValueError("Memory keys must contain between 1 and 200 characters.")
        if not normalized_value or len(normalized_value) > 4_000:
            raise ValueError("Memory values must contain between 1 and 4000 characters.")
        if _SECRET_LABEL.search(normalized_key) or _SECRET_LABEL.search(normalized_value):
            raise ValueError("FLEX does not store passwords, tokens, API keys, or secrets in memory.")
        if _SECRET_VALUE.search(normalized_value):
            raise ValueError("FLEX does not store credential-like values in memory.")
        return normalized_category, normalized_key, normalized_value

    @staticmethod
    def _memory_dict(row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "id": row[0],
            "category": row[1],
            "key": row[2],
            "value": row[3],
            "source": row[4],
            "confidence": row[5],
            "created_at": row[6],
            "updated_at": row[7],
        }

    @staticmethod
    def _memory_select() -> str:
        return (
            "SELECT id, category, key, value, source, confidence, created_at, updated_at "
            "FROM memories"
        )

    def save_memory(
        self,
        category: str,
        key: str,
        value: str,
        *,
        source: str = "explicit_user",
        confidence: float = 1.0,
    ) -> dict[str, Any]:
        category, key, value = self._validate_memory(category, key, value)
        if not source.strip() or len(source) > 100:
            raise ValueError("Memory source must contain between 1 and 100 characters.")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("Memory confidence must be between 0 and 1.")
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            existing = conn.execute(
                self._memory_select()
                + " WHERE category = ? AND key = ? COLLATE NOCASE",
                (category, key),
            ).fetchone()
            if existing is not None:
                existing_memory = self._memory_dict(existing)
                if existing_memory["value"].casefold() == value.casefold():
                    return {
                        "success": True,
                        "action": "duplicate",
                        "memory": existing_memory,
                    }
                raise ValueError(
                    "A memory with this category and key already exists; use memory_update to change it."
                )
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            cursor = conn.execute(
                """
                INSERT INTO memories
                    (category, key, value, source, confidence, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (category, key, value, source.strip(), confidence, now, now),
            )
            memory_id = cursor.lastrowid
            row = conn.execute(
                self._memory_select() + " WHERE id = ?",
                (memory_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Saved memory could not be read back from SQLite.")
        return {
            "success": True,
            "action": "saved",
            "memory": self._memory_dict(row),
        }

    def get_memory(
        self,
        *,
        memory_id: int | None = None,
        category: str | None = None,
        key: str | None = None,
    ) -> dict[str, Any]:
        if memory_id is not None:
            clause, parameters = "id = ?", (memory_id,)
        elif category and key:
            clause, parameters = "category = ? AND key = ? COLLATE NOCASE", (
                category.strip().lower(),
                key.strip(),
            )
        else:
            raise ValueError("Provide either a memory ID or both category and key.")
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            row = conn.execute(
                self._memory_select() + " WHERE " + clause,
                parameters,
            ).fetchone()
        if row is None:
            raise KeyError("No matching memory was found.")
        return self._memory_dict(row)

    def search_memories(
        self,
        query: str = "",
        *,
        category: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 20:
            raise ValueError("Memory search limit must be between 1 and 20.")
        conditions: list[str] = []
        parameters: list[Any] = []
        if query.strip():
            escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append(
                "(key LIKE ? ESCAPE '\\' COLLATE NOCASE OR "
                "value LIKE ? ESCAPE '\\' COLLATE NOCASE)"
            )
            parameters.extend((f"%{escaped}%", f"%{escaped}%"))
        if category is not None:
            if category not in MEMORY_CATEGORIES:
                raise ValueError(f"Unsupported memory category: {category!r}.")
            conditions.append("category = ?")
            parameters.append(category)
        statement = self._memory_select()
        if conditions:
            statement += " WHERE " + " AND ".join(conditions)
        statement += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        parameters.append(limit)
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            rows = conn.execute(statement, parameters).fetchall()
        return [self._memory_dict(row) for row in rows]

    def list_memories(self, *, limit: int = 500) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("Memory list limit must be between 1 and 1000.")
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            rows = conn.execute(
                self._memory_select() + " ORDER BY updated_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._memory_dict(row) for row in rows]

    def update_memory(
        self,
        memory_id: int,
        *,
        key: str,
        value: str,
        category: str,
        source: str = "explicit_user",
        confidence: float = 1.0,
    ) -> dict[str, Any]:
        category, key, value = self._validate_memory(category, key, value)
        if not source.strip() or len(source) > 100:
            raise ValueError("Memory source must contain between 1 and 100 characters.")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("Memory confidence must be between 0 and 1.")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            cursor = conn.execute(
                """
                UPDATE memories
                SET category = ?, key = ?, value = ?, source = ?, confidence = ?, updated_at = ?
                WHERE id = ?
                """,
                (category, key, value, source.strip(), confidence, now, memory_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Memory {memory_id} was not found.")
            row = conn.execute(
                self._memory_select() + " WHERE id = ?",
                (memory_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError(f"Updated memory {memory_id} could not be read back.")
        return self._memory_dict(row)

    def delete_memory(self, memory_id: int) -> None:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            cursor = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            if cursor.rowcount == 0:
                raise KeyError(f"Memory {memory_id} was not found.")
