from __future__ import annotations

import json
import logging
from typing import Any

import requests

from flex.ai.provider import AIProvider
from flex.ai.schemas import AIResponse, ToolCall

logger = logging.getLogger("flex.ai")


class OpenRouterProvider(AIProvider):
    def __init__(self, api_key: str, model: str = "openai/gpt-4o-mini", base_url: str = "https://openrouter.ai/api/v1") -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    def supports_tools(self) -> bool:
        return True

    def chat(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None = None) -> AIResponse:
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is not configured.")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://localhost",
            "X-Title": "FLEX Desktop Agent",
        }

        response = requests.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
        logger.info("OpenRouter response received.")
        return self._parse_response(data)

    @staticmethod
    def _parse_response(data: dict[str, Any]) -> AIResponse:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            error = data.get("error")
            if isinstance(error, dict):
                message = error.get("message")
            elif isinstance(error, str):
                message = error
            else:
                message = None
            if isinstance(message, str) and message:
                raise ValueError(f"OpenRouter returned an error: {message}")
            raise ValueError("OpenRouter response did not contain any choices.")

        choice = choices[0]
        message = choice.get("message", {})
        content = message.get("content") or ""

        calls: list[ToolCall] = []
        for tool_call in message.get("tool_calls", []) or []:
            function = tool_call.get("function", {})
            arguments = function.get("arguments") or {}
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            calls.append(
                ToolCall(
                    tool=function.get("name", ""),
                    arguments=arguments if isinstance(arguments, dict) else {},
                    id=tool_call.get("id"),
                )
            )

        return AIResponse(content=content, tool_calls=calls)
