from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AgentStatus(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    WAITING_FOR_PERMISSION = "waiting_for_permission"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class AgentState:
    status: AgentStatus = AgentStatus.IDLE
    last_tool: str | None = None
    last_result: dict | None = None
