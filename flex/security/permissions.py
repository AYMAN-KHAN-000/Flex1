from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Callable


class PermissionLevel(str, Enum):
    READ_ONLY = "READ_ONLY"
    LOW_RISK = "LOW_RISK"
    MEDIUM_RISK = "MEDIUM_RISK"
    HIGH_RISK = "HIGH_RISK"


@dataclass
class PermissionCheck:
    tool_name: str
    required_permission: PermissionLevel
    allowed: bool
    reason: str = ""


@dataclass(frozen=True)
class PermissionRequest:
    tool_name: str
    required_permission: PermissionLevel
    details: str
    description: str = ""


class PermissionManager:
    """Centralized permission gate for tool execution."""

    def __init__(
        self,
        require_confirmation: bool = True,
        confirmation_callback: Callable[[str, str], bool] | None = None,
    ) -> None:
        self.require_confirmation = require_confirmation
        self.confirmation_callback = confirmation_callback

    def can_execute_batch(
        self,
        requests: list[PermissionRequest],
    ) -> dict[tuple[str, str], PermissionCheck]:
        protected = [
            request
            for request in requests
            if request.required_permission == PermissionLevel.HIGH_RISK
        ]
        unique = {
            (request.tool_name, request.details): request
            for request in protected
        }
        if len(protected) < 2:
            return {}

        approved = not self.require_confirmation
        if self.require_confirmation and self.confirmation_callback is not None:
            action_details = [
                {
                    "action": request.description or request.tool_name,
                    "details": request.details,
                }
                for request in unique.values()
            ]
            approved = self.confirmation_callback(
                "FLEX workflow",
                json.dumps(action_details, ensure_ascii=False),
            )

        reason = (
            "User approved the protected workflow."
            if approved
            else "Protected workflow actions require user confirmation."
        )
        return {
            key: PermissionCheck(
                tool_name=request.tool_name,
                required_permission=request.required_permission,
                allowed=approved,
                reason=reason,
            )
            for key, request in unique.items()
        }

    def can_execute(
        self,
        tool_name: str,
        required_permission: PermissionLevel,
        details: str = "",
    ) -> PermissionCheck:
        if required_permission == PermissionLevel.HIGH_RISK:
            if not self.require_confirmation:
                return PermissionCheck(
                    tool_name=tool_name,
                    required_permission=required_permission,
                    allowed=True,
                    reason="High-risk action allowed by configuration.",
                )

            if self.confirmation_callback is not None and self.confirmation_callback(tool_name, details):
                return PermissionCheck(
                    tool_name=tool_name,
                    required_permission=required_permission,
                    allowed=True,
                    reason="User confirmed the high-risk action.",
                )

            return PermissionCheck(
                tool_name=tool_name,
                required_permission=required_permission,
                allowed=False,
                reason="High-risk actions require explicit user confirmation.",
            )

        return PermissionCheck(
            tool_name=tool_name,
            required_permission=required_permission,
            allowed=True,
            reason="Permission available.",
        )
