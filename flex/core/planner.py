from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PlanStep:
    description: str


class Planner:
    def plan(self, request: str) -> list[PlanStep]:
        cleaned = request.strip()
        if not cleaned:
            return []
        return [PlanStep(description=cleaned)]
