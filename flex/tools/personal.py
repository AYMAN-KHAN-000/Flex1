from __future__ import annotations

import os
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from flex.security.permissions import PermissionLevel
from flex.tools.app_control import _lookup_application
from flex.tools.registry import ToolSpec


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AddQuickFolderArgs(StrictArgs):
    name: str = Field(..., min_length=1, max_length=64)
    path: str = Field(..., min_length=1)


class NamedQuickFolderArgs(StrictArgs):
    name: str = Field(..., min_length=1, max_length=64)


class UpdateQuickFolderArgs(AddQuickFolderArgs):
    pass


class OpenQuickFolderArgs(NamedQuickFolderArgs):
    application: str | None = Field(
        default=None,
        description="Optional application for the folder, such as VS Code.",
    )


class NoteTextArgs(StrictArgs):
    text: str = Field(..., min_length=1, max_length=20_000)


class SearchNotesArgs(StrictArgs):
    query: str = Field(..., min_length=1, max_length=500)


class DeleteNoteArgs(SearchNotesArgs):
    confirm_delete: bool = Field(
        default=True,
        description="The user has explicitly asked to delete a matching note.",
    )


class LocalPersonalStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS quick_folders (
                    name TEXT PRIMARY KEY COLLATE NOCASE,
                    path TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS quick_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=10)

    @staticmethod
    def _clean_name(name: str) -> str:
        cleaned = name.strip()
        if not cleaned or any(character in cleaned for character in "\\/:*?\"<>|"):
            raise ValueError("Use a non-empty quick-folder name without path characters.")
        return cleaned

    @staticmethod
    def _existing_folder(path: str) -> Path:
        folder = Path(path).expanduser().resolve(strict=True)
        if not folder.is_dir():
            raise NotADirectoryError(f"'{path}' is not a folder.")
        return folder

    def add_quick_folder(self, name: str, path: str) -> dict[str, Any]:
        cleaned_name = self._clean_name(name)
        folder = self._existing_folder(path)
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO quick_folders (name, path, updated_at) VALUES (?, ?, ?)",
                    (cleaned_name, str(folder), datetime.now().astimezone().isoformat(timespec="seconds")),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    f"A quick folder named '{cleaned_name}' already exists. Remove it before adding a replacement."
                ) from exc
        return {"success": True, "action": "added", "name": cleaned_name, "path": str(folder)}

    def update_quick_folder(self, name: str, path: str) -> dict[str, Any]:
        cleaned_name = self._clean_name(name)
        folder = self._existing_folder(path)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE quick_folders SET path = ?, updated_at = ? WHERE name = ? COLLATE NOCASE",
                (str(folder), datetime.now().astimezone().isoformat(timespec="seconds"), cleaned_name),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"No quick folder named '{cleaned_name}' exists.")
        return {"success": True, "action": "updated", "name": cleaned_name, "path": str(folder)}

    def remove_quick_folder(self, name: str) -> dict[str, Any]:
        cleaned_name = self._clean_name(name)
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM quick_folders WHERE name = ? COLLATE NOCASE",
                (cleaned_name,),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"No quick folder named '{cleaned_name}' exists.")
        return {"success": True, "action": "removed", "name": cleaned_name}

    def get_quick_folders(self) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT name, path, updated_at FROM quick_folders ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return {
            "success": True,
            "folders": [
                {
                    "name": name,
                    "path": path,
                    "exists": Path(path).is_dir(),
                    "updated_at": updated_at,
                }
                for name, path, updated_at in rows
            ],
            "count": len(rows),
        }

    def get_quick_folder(self, name: str) -> Path:
        cleaned_name = self._clean_name(name)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT path FROM quick_folders WHERE name = ? COLLATE NOCASE",
                (cleaned_name,),
            ).fetchone()
        if row is None:
            raise KeyError(f"No quick folder named '{cleaned_name}' exists.")
        folder = Path(row[0])
        if not folder.is_dir():
            raise FileNotFoundError(
                f"The quick folder '{cleaned_name}' points to a folder that no longer exists: {folder}"
            )
        return folder

    def add_note(self, text: str) -> dict[str, Any]:
        cleaned_text = text.strip()
        if not cleaned_text:
            raise ValueError("A note cannot be empty.")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT id, created_at FROM quick_notes WHERE text = ? COLLATE NOCASE LIMIT 1",
                (cleaned_text,),
            ).fetchone()
            if existing is not None:
                return {
                    "success": True,
                    "action": "duplicate",
                    "note_id": existing[0],
                    "created_at": existing[1],
                    "text": cleaned_text,
                }
            cursor = connection.execute(
                "INSERT INTO quick_notes (text, created_at, updated_at) VALUES (?, ?, ?)",
                (cleaned_text, now, now),
            )
            note_id = cursor.lastrowid
        return {
            "success": True,
            "action": "saved",
            "note_id": note_id,
            "created_at": now,
            "text": cleaned_text,
        }

    def search_notes(
        self,
        query: str | None = None,
        today_only: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        sql = "SELECT id, text, created_at, updated_at FROM quick_notes"
        conditions: list[str] = []
        parameters: list[Any] = []
        if query:
            conditions.append("text LIKE ? ESCAPE '\\' COLLATE NOCASE")
            escaped_query = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            parameters.append(f"%{escaped_query}%")
        if today_only:
            conditions.append("substr(created_at, 1, 10) = ?")
            parameters.append(datetime.now().astimezone().date().isoformat())
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY created_at DESC LIMIT ?"
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return {
            "success": True,
            "notes": [
                {"id": note_id, "text": text, "created_at": created, "updated_at": updated}
                for note_id, text, created, updated in rows
            ],
            "count": len(rows),
        }

    def delete_note(self, query: str) -> dict[str, Any]:
        matches = self.search_notes(query)["notes"]
        if not matches:
            raise KeyError(f"No note matching '{query}' was found.")
        if len(matches) > 1:
            raise ValueError(
                "More than one note matches. Search notes and delete one by its exact note ID."
            )
        note = matches[0]
        with self._connect() as connection:
            connection.execute("DELETE FROM quick_notes WHERE id = ?", (note["id"],))
        return {"success": True, "action": "deleted", "note_id": note["id"], "text": note["text"]}


def _success_verifier(_arguments: dict[str, Any], result: dict[str, Any]) -> bool:
    return result.get("success") is True


def _make_tool(
    name: str,
    description: str,
    args_model: type[BaseModel],
    executor: Any,
    permission: PermissionLevel,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        schema=args_model.model_json_schema(),
        permission_level=permission,
        executor=executor,
        argument_model=args_model,
        verification=_success_verifier,
    )


def create_personal_tools(db_path: str | Path) -> list[ToolSpec]:
    store = LocalPersonalStore(db_path)

    def open_quick_folder(name: str, application: str | None = None) -> dict[str, Any]:
        folder = store.get_quick_folder(name)
        if application:
            resolved = _lookup_application(application)
            if not resolved:
                raise FileNotFoundError(f"Application '{application}' was not found.")
            if resolved.startswith(("ms-", "shell:AppsFolder\\")) or resolved.lower().endswith(".lnk"):
                raise RuntimeError(f"Application '{application}' cannot open a folder path.")
            process = subprocess.Popen([resolved, str(folder)], shell=False)
            return {
                "success": True,
                "action": "opened",
                "name": name,
                "path": str(folder),
                "application": application,
                "process_id": process.pid,
            }
        if os.name != "nt":
            raise OSError("Opening quick folders in File Explorer is only supported on Windows.")
        process = subprocess.Popen(["explorer.exe", str(folder)], shell=False)
        return {
            "success": True,
            "action": "opened",
            "name": name,
            "path": str(folder),
            "process_id": process.pid,
        }

    def list_notes() -> dict[str, Any]:
        return store.search_notes(limit=20)

    def list_today_notes() -> dict[str, Any]:
        return store.search_notes(today_only=True, limit=20)

    def delete_note(query: str, confirm_delete: bool = True) -> dict[str, Any]:
        if not confirm_delete:
            raise PermissionError("Note deletion requires explicit user confirmation.")
        return store.delete_note(query)

    return [
        _make_tool(
            "add_quick_folder",
            "Save an existing folder under a unique short name. Do not silently replace an existing shortcut.",
            AddQuickFolderArgs,
            store.add_quick_folder,
            PermissionLevel.LOW_RISK,
        ),
        _make_tool(
            "update_quick_folder",
            "Update an existing quick-folder shortcut to another existing folder.",
            UpdateQuickFolderArgs,
            store.update_quick_folder,
            PermissionLevel.LOW_RISK,
        ),
        _make_tool(
            "remove_quick_folder",
            "Remove a named quick-folder shortcut without deleting its target folder.",
            NamedQuickFolderArgs,
            store.remove_quick_folder,
            PermissionLevel.LOW_RISK,
        ),
        _make_tool(
            "list_quick_folders",
            "List saved quick folders and whether each target still exists.",
            StrictArgs,
            lambda: store.get_quick_folders(),
            PermissionLevel.READ_ONLY,
        ),
        _make_tool(
            "open_quick_folder",
            "Open a saved quick folder in File Explorer, or open it with a named application such as VS Code.",
            OpenQuickFolderArgs,
            open_quick_folder,
            PermissionLevel.LOW_RISK,
        ),
        _make_tool(
            "add_note",
            "Save one short personal note locally. Do not send or list all notes to the AI; check for duplicates.",
            NoteTextArgs,
            store.add_note,
            PermissionLevel.LOW_RISK,
        ),
        _make_tool(
            "search_notes",
            "Search local notes by a short query and return only matching notes.",
            SearchNotesArgs,
            store.search_notes,
            PermissionLevel.READ_ONLY,
        ),
        _make_tool(
            "list_notes",
            "Show the latest local notes (up to 100) when the user explicitly asks.",
            StrictArgs,
            list_notes,
            PermissionLevel.READ_ONLY,
        ),
        _make_tool(
            "list_today_notes",
            "Show only local notes created today when the user explicitly asks.",
            StrictArgs,
            list_today_notes,
            PermissionLevel.READ_ONLY,
        ),
        _make_tool(
            "delete_note",
            "Delete one uniquely matching local note only after explicit user confirmation. If several notes match, ask the user to identify one.",
            DeleteNoteArgs,
            delete_note,
            PermissionLevel.HIGH_RISK,
        ),
    ]
