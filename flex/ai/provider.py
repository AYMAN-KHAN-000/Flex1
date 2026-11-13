from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from flex.ai.schemas import AIResponse


class AIProvider(ABC):
    @abstractmethod
    def chat(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None) -> AIResponse:
        raise NotImplementedError

    @abstractmethod
    def supports_tools(self) -> bool:
        return True
