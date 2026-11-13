from __future__ import annotations

import pytest

from flex.ai.schemas import AIResponse, ToolCall


def test_ai_response_tracks_tool_calls():
    response = AIResponse(content="ok", tool_calls=[ToolCall(tool="open_application", arguments={"application": "notepad"})])
    assert response.has_tool_calls is True
    assert response.tool_calls[0].tool == "open_application"


def test_openrouter_response_parsing_handles_json_arguments():
    payload = {
        "choices": [
            {
                "message": {
                    "content": "Opening",
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "function": {
                                "name": "create_folder",
                                "arguments": '{"path": "C:/Users/Desktop/FLEX_TEST"}',
                            }
                        }
                    ],
                }
            }
        ]
    }
    response = __import__("flex.ai.openrouter", fromlist=["OpenRouterProvider"]).OpenRouterProvider._parse_response(payload)
    assert response.tool_calls[0].tool == "create_folder"
    assert response.tool_calls[0].id == "call_abc"
    assert response.tool_calls[0].arguments["path"].endswith("FLEX_TEST")


def test_openrouter_response_parsing_reports_api_error():
    from flex.ai.openrouter import OpenRouterProvider

    with pytest.raises(ValueError, match="OpenRouter returned an error: Invalid API key"):
        OpenRouterProvider._parse_response({"error": {"message": "Invalid API key"}})


def test_openrouter_chat_uses_bearer_api_key(monkeypatch):
    from flex.ai.openrouter import OpenRouterProvider

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "hello"}}]}

    def fake_post(url, *, json, headers, timeout):
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr("flex.ai.openrouter.requests.post", fake_post)
    OpenRouterProvider(api_key="test-key").chat([{"role": "user", "content": "hello"}])

    assert captured["headers"]["Authorization"] == "Bearer test-key"
