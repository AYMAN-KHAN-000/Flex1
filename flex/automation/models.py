from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AutomationStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=64)
    tool: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int | None = Field(default=None, ge=1000, le=300000)
    continue_on_failure: bool = False


class WorkflowTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["phrase"] = "phrase"
    value: str = Field(min_length=1, max_length=200)


class AutomationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    trigger: WorkflowTrigger | None = None
    steps: list[AutomationStep] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_unique_step_ids(self) -> "AutomationRequest":
        step_ids = [step.id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("Automation step IDs must be unique.")
        return self
