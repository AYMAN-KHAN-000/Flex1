from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class ConversationContext:
    messages: list[ChatMessage] = field(default_factory=list)

    def add_user_message(self, text: str) -> None:
        self.messages.append(ChatMessage(role="user", content=text))

    def add_assistant_message(self, text: str) -> None:
        self.messages.append(ChatMessage(role="assistant", content=text))

    def to_provider_messages(self) -> list[dict[str, Any]]:
        return [{"role": message.role, "content": message.content} for message in self.messages]
