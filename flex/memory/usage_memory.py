from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_DOCUMENT_EXTENSIONS = {
    ".doc",
    ".docx",
    ".csv",
    ".md",
    ".odt",
    ".pdf",
    ".ppt",
    ".pptx",
    ".rtf",
    ".txt",
    ".xls",
    ".xlsx",
}
_VIDEO_EXTENSIONS = {
    ".3gp",
    ".avi",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".webm",
    ".wmv",
}
_AUDIO_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mid",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
    ".wma",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Usage memory only accepts HTTP or HTTPS website URLs.")
    hostname = parsed.hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    return urlunsplit((parsed.scheme, hostname, "", "", ""))


def classify_path(path: str | Path) -> str:
    target = Path(path)
    if target.is_dir():
        return "folder"
    extension = target.suffix.lower()
    if extension == ".pdf":
        return "pdf"
    if extension in _DOCUMENT_EXTENSIONS:
        return "document"
    if extension in _VIDEO_EXTENSIONS:
        return "video"
    if extension in _AUDIO_EXTENSIONS:
        return "audio"
    return "file"


class UsageMemory:
    """Local metadata for explicitly launched items; never reads file contents."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _initialize(self) -> None:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_key TEXT NOT NULL UNIQUE,
                    item_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    location TEXT NOT NULL,
                    application TEXT,
                    first_seen TEXT NOT NULL,
                    last_opened TEXT,
                    open_count INTEGER NOT NULL DEFAULT 0 CHECK (open_count >= 0),
                    tags TEXT NOT NULL DEFAULT '[]',
                    category TEXT,
                    source TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS usage_items_type_recent "
                "ON usage_items(item_type, last_opened DESC)"
            )

    @staticmethod
    def _row_to_item(row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "id": row[0],
            "item_type": row[1],
            "name": row[2],
            "location": row[3],
            "application": row[4],
            "first_seen": row[5],
            "last_opened": row[6],
            "open_count": row[7],
            "tags": json.loads(row[8]),
            "category": row[9],
            "source": row[10],
        }

    @staticmethod
    def _select() -> str:
        return (
            "SELECT id, item_type, name, location, application, first_seen, "
            "last_opened, open_count, tags, category, source FROM usage_items"
        )

    @staticmethod
    def _path_key(path: str | Path) -> str:
        return os.path.normcase(str(Path(path).expanduser().resolve(strict=False)))

    def record_open(
        self,
        *,
        item_type: str,
        name: str,
        location: str,
        application: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        source: str = "flex_action",
    ) -> dict[str, Any]:
        normalized_type = item_type.strip().lower()
        normalized_name = name.strip()
        normalized_raw_location = location.strip()
        normalized_source = source.strip()
        if not normalized_raw_location or len(normalized_raw_location) > 4096:
            raise ValueError("Usage item location must contain between 1 and 4096 characters.")
        normalized_location = (
            _safe_url(normalized_raw_location)
            if normalized_type == "website"
            else str(Path(normalized_raw_location).expanduser().resolve(strict=False))
        )
        if not normalized_type or len(normalized_type) > 40:
            raise ValueError("Usage item type must contain between 1 and 40 characters.")
        if not normalized_name or len(normalized_name) > 500:
            raise ValueError("Usage item name must contain between 1 and 500 characters.")
        if not normalized_source or len(normalized_source) > 100:
            raise ValueError("Usage item source must contain between 1 and 100 characters.")
        if application is not None and len(application) > 500:
            raise ValueError("Usage application name cannot exceed 500 characters.")
        if category is not None and len(category) > 100:
            raise ValueError("Usage category cannot exceed 100 characters.")
        normalized_tags = sorted(
            {tag.strip() for tag in (tags or []) if tag.strip()}
        )
        if any(len(tag) > 100 for tag in normalized_tags):
            raise ValueError("Usage tags cannot exceed 100 characters.")

        key = (
            f"website:{normalized_location.casefold()}"
            if normalized_type == "website"
            else f"{normalized_type}:{self._path_key(normalized_location)}"
            if normalized_type in {"application", "file", "folder", "pdf", "document", "video", "audio"}
            else f"{normalized_type}:{normalized_location.casefold()}"
        )
        now = _now()
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute(
                """
                INSERT INTO usage_items
                    (item_key, item_type, name, location, application, first_seen,
                     last_opened, open_count, tags, category, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(item_key) DO UPDATE SET
                    name = excluded.name,
                    application = COALESCE(excluded.application, usage_items.application),
                    last_opened = excluded.last_opened,
                    open_count = usage_items.open_count + 1,
                    tags = excluded.tags,
                    category = COALESCE(excluded.category, usage_items.category),
                    source = excluded.source
                """,
                (
                    key,
                    normalized_type,
                    normalized_name,
                    normalized_location,
                    application,
                    now,
                    now,
                    json.dumps(normalized_tags),
                    category,
                    normalized_source,
                ),
            )
            row = conn.execute(self._select() + " WHERE item_key = ?", (key,)).fetchone()
        if row is None:
            raise RuntimeError("Usage record could not be read back from SQLite.")
        return self._row_to_item(row)

    def index_study_folder(self, folder: str | Path) -> int:
        root = Path(folder).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("A study folder must be an existing directory.")
        indexed = 0
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in _DOCUMENT_EXTENSIONS:
                    continue
                normalized = str(path.resolve(strict=False))
                item_type = classify_path(path)
                key = f"{item_type}:{self._path_key(path)}"
                conn.execute(
                    """
                    INSERT INTO usage_items
                        (item_key, item_type, name, location, first_seen,
                         open_count, tags, category, source)
                    VALUES (?, ?, ?, ?, ?, 0, '[]', 'study', 'study_folder')
                    ON CONFLICT(item_key) DO UPDATE SET category = 'study'
                    """,
                    (key, item_type, path.name, normalized, _now()),
                )
                indexed += 1
        return indexed

    def list_items(
        self,
        *,
        item_type: str | None = None,
        sort: str = "recent",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("Usage item limit must be between 1 and 1000.")
        if sort not in {"recent", "frequent", "relevant"}:
            raise ValueError("Unsupported usage item sort order.")
        where = " WHERE item_type = ?" if item_type else ""
        parameters: list[Any] = [item_type] if item_type else []
        ordering = {
            "recent": "last_opened DESC, first_seen DESC",
            "frequent": "open_count DESC, last_opened DESC",
            "relevant": "open_count DESC, last_opened DESC",
        }[sort]
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            rows = conn.execute(
                self._select() + where + " ORDER BY " + ordering + " LIMIT ?",
                (*parameters, limit),
            ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def search(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("Usage search limit must be between 1 and 100.")
        cleaned = query.strip()
        if not cleaned:
            return self.list_items(sort="recent", limit=limit)
        escaped = cleaned.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            rows = conn.execute(
                self._select()
                + """
                WHERE name LIKE ? ESCAPE '\\' COLLATE NOCASE
                   OR location LIKE ? ESCAPE '\\' COLLATE NOCASE
                   OR item_type LIKE ? ESCAPE '\\' COLLATE NOCASE
                   OR category LIKE ? ESCAPE '\\' COLLATE NOCASE
                   OR tags LIKE ? ESCAPE '\\' COLLATE NOCASE
                ORDER BY
                    CASE WHEN name LIKE ? ESCAPE '\\' COLLATE NOCASE THEN 0
                         WHEN category LIKE ? ESCAPE '\\' COLLATE NOCASE
                           OR tags LIKE ? ESCAPE '\\' COLLATE NOCASE THEN 1
                         ELSE 2 END,
                    open_count DESC, last_opened DESC
                LIMIT ?
                """,
                (
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                    pattern,
                    limit,
                ),
            ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def delete_item(self, item_id: int) -> None:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            cursor = conn.execute("DELETE FROM usage_items WHERE id = ?", (item_id,))
        if cursor.rowcount == 0:
            raise KeyError(f"Usage item {item_id} was not found.")

    def clear(self) -> int:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            cursor = conn.execute("DELETE FROM usage_items")
        return cursor.rowcount
