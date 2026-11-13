from __future__ import annotations

import sqlite3
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from flex.memory.database import MemoryDatabase
from flex.security.permissions import PermissionLevel
from flex.tools.registry import ToolSpec


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SaveMemoryArgs(StrictArgs):
    category: Literal[
        "preference", "fact", "project", "shortcut", "decision", "workflow_context"
    ]
    key: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=4000)


class SearchMemoryArgs(StrictArgs):
    query: str = Field(default="", max_length=500)
    category: Literal[
        "preference", "fact", "project", "shortcut", "decision", "workflow_context"
    ] | None = None
    limit: int = Field(default=10, ge=1, le=20)


class GetMemoryArgs(StrictArgs):
    memory_id: int | None = Field(default=None, ge=1)
    category: Literal[
        "preference", "fact", "project", "shortcut", "decision", "workflow_context"
    ] | None = None
    key: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def require_one_lookup(self) -> "GetMemoryArgs":
        if self.memory_id is None and (self.category is None or self.key is None):
            raise ValueError("Provide a memory ID or both category and key.")
        if self.memory_id is not None and (self.category is not None or self.key is not None):
            raise ValueError("Use either a memory ID or a category/key pair, not both.")
        return self


class UpdateMemoryArgs(SaveMemoryArgs):
    memory_id: int = Field(ge=1)


class DeleteMemoryArgs(StrictArgs):
    memory_id: int = Field(ge=1)


class ListMemoryArgs(StrictArgs):
    category: Literal[
        "preference", "fact", "project", "shortcut", "decision", "workflow_context"
    ] | None = None
    limit: int = Field(default=20, ge=1, le=20)


def _error(code: str, message: str) -> dict[str, Any]:
    return {
        "success": False,
        "error": {"code": code, "message": message, "retryable": False},
    }


def _tool(
    name: str,
    description: str,
    model: type[BaseModel],
    executor: Any,
    permission: PermissionLevel,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        schema=model.model_json_schema(),
        permission_level=permission,
        executor=executor,
        argument_model=model,
        verification=lambda _arguments, result: result.get("success") is True,
    )


def create_memory_tools(
    db_path: str,
    *,
    enabled: bool = True,
    require_confirmation: bool = True,
) -> list[ToolSpec]:
    database = MemoryDatabase(db_path)

    def enabled_or_error(action: Any) -> Any:
        def execute(**arguments: Any) -> dict[str, Any]:
            if not enabled:
                return _error("MEMORY_DISABLED", "Persistent memory is disabled in FLEX settings.")
            try:
                result = action(**arguments)
                return result if isinstance(result, dict) else {"success": True, "memory": result}
            except KeyError as exc:
                return _error("MEMORY_NOT_FOUND", str(exc))
            except ValueError as exc:
                return _error("MEMORY_INVALID_DATA", str(exc))
            except sqlite3.Error:
                return _error("MEMORY_DATABASE_ERROR", "SQLite could not complete the memory operation.")

        return execute

    write_permission = (
        PermissionLevel.HIGH_RISK if require_confirmation else PermissionLevel.LOW_RISK
    )

    def save(category: str, key: str, value: str) -> dict[str, Any]:
        return database.save_memory(category, key, value)

    def search(
        query: str = "",
        category: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "memories": database.search_memories(query, category=category, limit=limit),
        }

    def get(
        memory_id: int | None = None,
        category: str | None = None,
        key: str | None = None,
    ) -> dict[str, Any]:
        return {"success": True, "memory": database.get_memory(memory_id=memory_id, category=category, key=key)}

    def update(
        category: str,
        key: str,
        value: str,
        memory_id: int,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "action": "updated",
            "memory": database.update_memory(
                memory_id,
                category=category,
                key=key,
                value=value,
            ),
        }

    def delete(memory_id: int) -> dict[str, Any]:
        database.delete_memory(memory_id)
        return {"success": True, "action": "deleted", "memory_id": memory_id}

    def list_memories(
        category: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "memories": database.search_memories("", category=category, limit=limit),
        }

    return [
        _tool("memory_save", "Save a long-term preference, fact, project, shortcut, decision, or workflow context only when explicitly asked. Never store credentials.", SaveMemoryArgs, enabled_or_error(save), write_permission),
        _tool("memory_search", "Search a limited set of relevant local memories; never retrieve the entire database by default.", SearchMemoryArgs, enabled_or_error(search), PermissionLevel.READ_ONLY),
        _tool("memory_get", "Retrieve one local memory by ID or exact category and key.", GetMemoryArgs, enabled_or_error(get), PermissionLevel.READ_ONLY),
        _tool("memory_update", "Update one existing memory by ID; do not overwrite a different key implicitly.", UpdateMemoryArgs, enabled_or_error(update), write_permission),
        _tool("memory_delete", "Delete one local memory by ID after explicit confirmation.", DeleteMemoryArgs, enabled_or_error(delete), PermissionLevel.HIGH_RISK),
        _tool("memory_list", "List at most 20 local memories, optionally filtered by category, only when the user explicitly asks.", ListMemoryArgs, enabled_or_error(list_memories), PermissionLevel.READ_ONLY),
    ]
