from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from flex.security.permissions import PermissionLevel


@dataclass
class ToolSpec:
    name: str
    description: str
    schema: dict[str, Any]
    permission_level: PermissionLevel
    executor: Callable[..., dict[str, Any]]
    argument_model: type[BaseModel] | None = None
    verification: Callable[[dict[str, Any], dict[str, Any]], bool] | None = None
    timeout: float = 15.0
    error_handler: Callable[[Exception], dict[str, Any]] | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, tool: ToolSpec) -> None:
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' is not registered.")
        return self._tools[name]

    def list(self) -> list[str]:
        return sorted(self._tools)

    def as_openai_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.schema,
                },
            }
            for tool in self._tools.values()
        ]

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __iter__(self):
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    @property
    def tools(self) -> dict[str, ToolSpec]:
        return self._tools.copy()
