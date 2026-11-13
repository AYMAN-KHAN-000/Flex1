from __future__ import annotations

from flex.tools.registry import ToolSpec, ToolRegistry


class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def execute(self, tool_name: str, arguments: dict) -> dict:
        tool: ToolSpec = self.registry.get(tool_name)
        if tool.executor is None:
            raise ValueError(f"Tool '{tool_name}' has no executor.")
        return tool.executor(**arguments)

    def verify(
        self,
        tool_name: str,
        arguments: dict,
        result: dict,
    ) -> bool:
        tool = self.registry.get(tool_name)
        if tool.verification is not None:
            return tool.verification(arguments, result)
        return False
