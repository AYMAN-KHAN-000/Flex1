from __future__ import annotations

from copy import deepcopy

from flex.core.executor import ToolExecutor
from flex.core.agent import FLEXAgent, MAX_TOOL_CALLS
from flex.core.state import AgentStatus
from flex.security.permissions import PermissionLevel, PermissionManager
from flex.tools.app_control import OPEN_APPLICATION_TOOL
from flex.tools.filesystem import CREATE_FILE_TOOL, CREATE_FOLDER_TOOL, OPEN_FILE_TOOL
from flex.tools.registry import ToolRegistry
from flex.ai.schemas import AIResponse, ToolCall


class FakeProvider:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, *, tools=None):
        self.calls += 1

        class Response:
            content = ""
            tool_calls = []
            has_tool_calls = False

        if self.calls == 1:
            Response.content = "opening app"
            Response.tool_calls = [
                {"tool": "open_application", "arguments": {"application": "notepad"}}
            ]
            Response.has_tool_calls = True

        return Response()


def test_agent_process_request_executes_tool():
    registry = ToolRegistry()
    registry.register(
        type(OPEN_APPLICATION_TOOL)(
            name=OPEN_APPLICATION_TOOL.name,
            description=OPEN_APPLICATION_TOOL.description,
            schema=OPEN_APPLICATION_TOOL.schema,
            permission_level=OPEN_APPLICATION_TOOL.permission_level,
            executor=lambda application: {"status": "opened", "application": application},
            argument_model=OPEN_APPLICATION_TOOL.argument_model,
            verification=lambda arguments, result: result.get("status") == "opened",
        )
    )
    agent = FLEXAgent(provider=FakeProvider(), registry=registry, permission_manager=PermissionManager(require_confirmation=False))
    response = agent.process_request("Open Notepad")
    assert response == "Done."
    assert agent.state.last_result == {"status": "opened", "application": "notepad"}


def test_voice_request_uses_the_same_response_prompt_as_text_chat():
    class CapturingProvider:
        def __init__(self):
            self.messages = []

        def chat(self, messages, *, tools=None):
            self.messages.append(deepcopy(messages))
            return AIResponse(content="Done.")

    voice_provider = CapturingProvider()
    voice_agent = FLEXAgent(
        provider=voice_provider,
        registry=ToolRegistry(),
        permission_manager=PermissionManager(require_confirmation=False),
    )
    text_provider = CapturingProvider()
    text_agent = FLEXAgent(
        provider=text_provider,
        registry=ToolRegistry(),
        permission_manager=PermissionManager(require_confirmation=False),
    )

    assert voice_agent.process_voice_request("Open Notepad") == "Done."
    assert text_agent.process_request("Open Notepad") == "Done."

    voice_messages = voice_provider.messages[0]
    text_messages = text_provider.messages[0]
    system_message = voice_messages[0]
    assert system_message["role"] == "system"
    assert (
        "Do not claim an action succeeded unless its tool call succeeds."
        in system_message["content"]
    )
    assert voice_messages == text_messages
    assert voice_messages[-1] == {
        "role": "user",
        "content": "Open Notepad",
    }


def test_agent_identity_prompt_separates_creator_from_configured_ai_engine(monkeypatch):
    from flex.ai.openrouter import OpenRouterProvider

    captured_messages = []
    provider = OpenRouterProvider(
        api_key="test-key",
        model="openai/configured-model",
    )
    monkeypatch.setattr(
        provider,
        "chat",
        lambda messages, *, tools=None: (
            captured_messages.append(deepcopy(messages))
            or AIResponse(
                content=(
                    "I'm FLEX, your personal AI desktop assistant, designed to help you "
                    "with intelligent conversations, PC control, coding, automation, and productivity."
                )
            )
        ),
    )
    agent = FLEXAgent(
        provider=provider,
        registry=ToolRegistry(),
        permission_manager=PermissionManager(require_confirmation=False),
    )

    assert agent.process_request("Who are you?") == (
        "I'm FLEX, your personal AI desktop assistant, designed to help you "
        "with intelligent conversations, PC control, coding, automation, and productivity."
    )
    system_prompt = captured_messages[0][0]["content"]
    assert "created and developed by Ayman Khan" in system_prompt
    assert "configured AI provider is OpenRouter" in system_prompt
    assert "configured model is openai/configured-model" in system_prompt
    assert "never call an AI provider FLEX's creator" in system_prompt


def test_creator_information_answers_always_include_official_links():
    agent = FLEXAgent(
        provider=type(
            "CreatorInfoProvider",
            (),
            {
                "chat": lambda self, messages, *, tools=None: AIResponse(
                    content="I was created by Ayman Khan."
                )
            },
        )(),
        registry=ToolRegistry(),
        permission_manager=PermissionManager(require_confirmation=False),
    )

    answer = agent.process_request("Ayman Khan details.")

    assert "I was created by Ayman Khan." in answer
    assert "https://aymanweb.vercel.app" in answer
    assert "https://aymanbio.vercel.app" in answer
    assert "https://github.com/AYMAN-KHAN-000" in answer


def test_empty_ai_responses_retry_once_then_use_contextual_fallbacks():
    cases = (
        (
            "hi",
            "Hi! I'm FLEX, your personal AI desktop assistant. "
            "What can I help you with?",
        ),
        ("what?", "Could you clarify what you'd like to know or do?"),
        (
            "Tell me about space.",
            "I couldn't generate a response just now. Please try again.",
        ),
    )
    for request, expected in cases:
        class EmptyProvider:
            calls = 0

            def chat(self, _messages, *, tools=None):
                self.calls += 1
                return AIResponse()

        provider = EmptyProvider()
        agent = FLEXAgent(
            provider=provider,
            registry=ToolRegistry(),
            permission_manager=PermissionManager(require_confirmation=False),
        )

        assert agent.process_request(request) == expected
        assert provider.calls == 2


def test_agent_replaces_unhelpful_generic_replies_for_greetings_and_clarifications():
    cases = (
        (
            "hi",
            "Hi! I'm FLEX, your personal AI desktop assistant. "
            "What can I help you with?",
        ),
        ("what?", "Could you clarify what you'd like to know or do?"),
    )
    for request, expected in cases:
        class GenericProvider:
            def chat(self, _messages, *, tools=None):
                return AIResponse(content="I can help with that.")

        agent = FLEXAgent(
            provider=GenericProvider(),
            registry=ToolRegistry(),
            permission_manager=PermissionManager(require_confirmation=False),
        )

        assert agent.process_request(request) == expected


def test_agent_continues_tool_calls_until_file_is_created_and_opened(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from flex.tools import app_control, filesystem

    target = tmp_path / "main.py"
    source = 'print("tic tac toe")\n'
    monkeypatch.setattr(filesystem, "_safe_directory", lambda _path: target)
    monkeypatch.setattr(app_control, "_lookup_application", lambda _application: "code.exe")
    monkeypatch.setattr(
        filesystem.subprocess,
        "Popen",
        lambda _args, **_kwargs: SimpleNamespace(pid=1234),
    )

    responses = [
        AIResponse(tool_calls=[
            ToolCall(id="call_open", tool="open_application", arguments={"application": "VS Code"})
        ]),
        AIResponse(tool_calls=[
            ToolCall(id="call_create", tool="create_file", arguments={"path": "main.py", "content": source})
        ]),
        AIResponse(tool_calls=[
            ToolCall(
                id="call_file",
                tool="open_file",
                arguments={"path": "main.py", "application": "VS Code"},
            )
        ]),
        AIResponse(content="Created and opened main.py in VS Code."),
    ]

    class MultiStepProvider:
        def __init__(self):
            self.messages = []

        def chat(self, messages, *, tools=None):
            self.messages.append(deepcopy(messages))
            return responses.pop(0)

    provider = MultiStepProvider()
    registry = ToolRegistry()
    for tool in (OPEN_APPLICATION_TOOL, CREATE_FILE_TOOL, OPEN_FILE_TOOL):
        registry.register(tool)
    agent = FLEXAgent(
        provider=provider,
        registry=registry,
        permission_manager=PermissionManager(require_confirmation=False),
    )

    result = agent.process_request(
        "Open VS Code, create a file named main.py, and make a Python tic tac toe game in it."
    )

    assert result == "Created and opened main.py in VS Code."
    assert target.read_text(encoding="utf-8") == source
    assert len(provider.messages) == 4
    assert provider.messages[1][-1] == {
        "role": "tool",
        "tool_call_id": "call_open",
        "content": '{"status": "opened", "application": "VS Code", "path": "code.exe", "process_id": 1234}',
    }
    assert provider.messages[2][-1]["tool_call_id"] == "call_create"
    assert '"status": "created"' in provider.messages[2][-1]["content"]
    assert provider.messages[3][-1]["tool_call_id"] == "call_file"
    assert '"status": "opened"' in provider.messages[3][-1]["content"]


def test_open_application_verifier_receives_request_and_result_separately(monkeypatch):
    from types import SimpleNamespace

    from flex.tools import app_control

    monkeypatch.setattr(
        app_control.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout='"notepad.exe","1234","Console","1","10 K"'),
    )
    registry = ToolRegistry()
    registry.register(OPEN_APPLICATION_TOOL)
    executor = ToolExecutor(registry)

    assert executor.verify(
        "open_application",
        {"application": "notepad"},
        {"status": "opened", "application": "notepad", "path": "notepad.exe"},
    ) is True


def test_agent_rejects_invalid_tool_arguments_before_execution():
    from types import SimpleNamespace

    registry = ToolRegistry()
    called = False

    def executor(path):
        nonlocal called
        called = True
        return {"status": "created", "path": path}

    registry.register(
        type(CREATE_FOLDER_TOOL)(
            name="create_folder",
            description=CREATE_FOLDER_TOOL.description,
            schema=CREATE_FOLDER_TOOL.schema,
            permission_level=CREATE_FOLDER_TOOL.permission_level,
            executor=executor,
            argument_model=CREATE_FOLDER_TOOL.argument_model,
        )
    )
    responses = [
        SimpleNamespace(
            has_tool_calls=True,
            tool_calls=[{"tool": "create_folder", "arguments": {}}],
            content="",
        ),
        SimpleNamespace(has_tool_calls=False, tool_calls=[], content=""),
    ]
    agent = FLEXAgent(
        provider=type(
            "InvalidProvider",
            (),
            {"chat": lambda self, messages, *, tools=None: responses.pop(0)},
        )(),
        registry=registry,
        permission_manager=PermissionManager(require_confirmation=False),
    )

    response = agent.process_request("Create a folder")
    assert response == "I couldn't complete that operation."
    assert called is False


def test_agent_requests_confirmation_for_high_risk_action():
    from types import SimpleNamespace

    from flex.tools.system_control import WINDOWS_POWER_ACTION_TOOL

    actions = []
    registry = ToolRegistry()
    registry.register(
        type(WINDOWS_POWER_ACTION_TOOL)(
            name=WINDOWS_POWER_ACTION_TOOL.name,
            description=WINDOWS_POWER_ACTION_TOOL.description,
            schema=WINDOWS_POWER_ACTION_TOOL.schema,
            permission_level=WINDOWS_POWER_ACTION_TOOL.permission_level,
            executor=lambda action: actions.append(action) or {"status": "started", "action": action},
            argument_model=WINDOWS_POWER_ACTION_TOOL.argument_model,
            verification=WINDOWS_POWER_ACTION_TOOL.verification,
        )
    )
    prompts = []
    responses = [
        SimpleNamespace(
            has_tool_calls=True,
            tool_calls=[
                {"tool": "windows_power_action", "arguments": {"action": "restart"}}
            ],
            content="",
        ),
        SimpleNamespace(has_tool_calls=False, tool_calls=[], content=""),
    ]
    agent = FLEXAgent(
        provider=type(
            "RestartProvider",
            (),
            {"chat": lambda self, messages, *, tools=None: responses.pop(0)},
        )(),
        registry=registry,
        permission_manager=PermissionManager(
            confirmation_callback=lambda tool_name, details: prompts.append((tool_name, details)) or True
        ),
    )

    assert agent.process_request("Restart this computer") == "Done."
    assert actions == ["restart"]
    assert prompts == [("windows_power_action", '{"action": "restart"}')]


def test_agent_does_not_run_high_risk_action_when_confirmation_is_denied():
    from types import SimpleNamespace

    from flex.tools.system_control import WINDOWS_POWER_ACTION_TOOL

    actions = []
    registry = ToolRegistry()
    registry.register(
        type(WINDOWS_POWER_ACTION_TOOL)(
            name=WINDOWS_POWER_ACTION_TOOL.name,
            description=WINDOWS_POWER_ACTION_TOOL.description,
            schema=WINDOWS_POWER_ACTION_TOOL.schema,
            permission_level=WINDOWS_POWER_ACTION_TOOL.permission_level,
            executor=lambda action: actions.append(action) or {"status": "started", "action": action},
            argument_model=WINDOWS_POWER_ACTION_TOOL.argument_model,
            verification=WINDOWS_POWER_ACTION_TOOL.verification,
        )
    )
    responses = [
        SimpleNamespace(
            has_tool_calls=True,
            tool_calls=[
                {"tool": "windows_power_action", "arguments": {"action": "shutdown"}}
            ],
            content="",
        ),
        SimpleNamespace(has_tool_calls=False, tool_calls=[], content=""),
    ]
    agent = FLEXAgent(
        provider=type(
            "ShutdownProvider",
            (),
            {"chat": lambda self, messages, *, tools=None: responses.pop(0)},
        )(),
        registry=registry,
        permission_manager=PermissionManager(confirmation_callback=lambda _tool, _details: False),
    )

    response = agent.process_request("Shut down this computer")
    assert response == "I couldn't proceed because the action wasn't approved."
    assert actions == []


def test_agent_groups_confirmations_for_protected_tools_in_one_response():
    from types import SimpleNamespace

    from flex.tools.app_control import CLOSE_APPLICATION_TOOL

    actions = []
    registry = ToolRegistry()
    registry.register(
        type(CLOSE_APPLICATION_TOOL)(
            name=CLOSE_APPLICATION_TOOL.name,
            description=CLOSE_APPLICATION_TOOL.description,
            schema=CLOSE_APPLICATION_TOOL.schema,
            permission_level=CLOSE_APPLICATION_TOOL.permission_level,
            executor=lambda application: actions.append(application)
            or {"status": "closed", "application": application},
            argument_model=CLOSE_APPLICATION_TOOL.argument_model,
            verification=lambda _arguments, result: result.get("status") == "closed",
        )
    )
    prompts = []
    responses = [
        SimpleNamespace(
            has_tool_calls=True,
            tool_calls=[
                {"tool": "close_application", "arguments": {"application": "Notepad"}},
                {"tool": "close_application", "arguments": {"application": "Chrome"}},
            ],
            content="",
        ),
        SimpleNamespace(has_tool_calls=False, tool_calls=[], content=""),
    ]
    agent = FLEXAgent(
        provider=type(
            "CloseApplicationsProvider",
            (),
            {"chat": lambda self, messages, *, tools=None: responses.pop(0)},
        )(),
        registry=registry,
        permission_manager=PermissionManager(
            confirmation_callback=lambda tool_name, details: (
                prompts.append((tool_name, details)) or True
            )
        ),
    )

    assert agent.process_request("Close Notepad and Chrome.") == "Done."
    assert actions == ["Notepad", "Chrome"]
    assert len(prompts) == 1
    assert prompts[0][0] == "FLEX workflow"
    assert "Notepad" in prompts[0][1] and "Chrome" in prompts[0][1]


def test_agent_does_not_repeat_an_identical_failed_tool_call():
    calls = []
    tool = type(CREATE_FOLDER_TOOL)(
        name=CREATE_FOLDER_TOOL.name,
        description=CREATE_FOLDER_TOOL.description,
        schema=CREATE_FOLDER_TOOL.schema,
        permission_level=CREATE_FOLDER_TOOL.permission_level,
        executor=lambda path: calls.append(path) or {
            "success": False,
            "error": {"message": "Access denied."},
        },
        argument_model=CREATE_FOLDER_TOOL.argument_model,
        verification=lambda _arguments, _result: True,
    )
    registry = ToolRegistry()
    registry.register(tool)
    failed_call = ToolCall(
        id="call_create",
        tool="create_folder",
        arguments={"path": "Desktop/Projects"},
    )
    responses = [
        AIResponse(tool_calls=[failed_call]),
        AIResponse(tool_calls=[failed_call]),
        AIResponse(content="I couldn't complete the requested operation."),
    ]

    class RepeatingProvider:
        def __init__(self):
            self.messages = []

        def chat(self, messages, *, tools=None):
            self.messages.append(deepcopy(messages))
            return responses.pop(0)

    provider = RepeatingProvider()
    agent = FLEXAgent(
        provider=provider,
        registry=registry,
        permission_manager=PermissionManager(require_confirmation=False),
    )

    assert agent.process_request("Create a Projects folder on my desktop.") == (
        "I couldn't complete the requested operation."
    )
    assert calls == ["Desktop/Projects"]
    assert "REPEATED_FAILED_OPERATION" in provider.messages[2][-1]["content"]
    assert agent.state.status == AgentStatus.FAILED


def test_agent_enforces_a_total_tool_call_limit_and_returns_clean_fallback():
    calls = []
    tool = type(CREATE_FOLDER_TOOL)(
        name=CREATE_FOLDER_TOOL.name,
        description=CREATE_FOLDER_TOOL.description,
        schema=CREATE_FOLDER_TOOL.schema,
        permission_level=CREATE_FOLDER_TOOL.permission_level,
        executor=lambda path: calls.append(path) or {"success": True},
        argument_model=CREATE_FOLDER_TOOL.argument_model,
        verification=lambda _arguments, _result: True,
    )
    registry = ToolRegistry()
    registry.register(tool)

    class RepeatingProvider:
        def __init__(self):
            self.calls = 0

        def chat(self, _messages, *, tools=None):
            self.calls += 1
            return AIResponse(
                tool_calls=[
                    ToolCall(
                        id=f"call_{self.calls}_{index}",
                        tool="create_folder",
                        arguments={"path": f"Desktop/Folder{index}"},
                    )
                    for index in range(4)
                ]
            )

    provider = RepeatingProvider()
    agent = FLEXAgent(
        provider=provider,
        registry=registry,
        permission_manager=PermissionManager(require_confirmation=False),
    )

    response = agent.process_request("Create folders.")
    assert response == "I couldn't safely finish that request within the execution limit."
    assert len(calls) == MAX_TOOL_CALLS
    assert "create_folder" not in response
    assert agent.state.status == AgentStatus.FAILED


def test_executor_requires_positive_evidence_when_no_custom_verifier_exists():
    from flex.tools.registry import ToolSpec

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="unverified",
            description="Test tool",
            schema={"type": "object"},
            permission_level=PermissionLevel.READ_ONLY,
            executor=lambda: {},
        )
    )
    executor = ToolExecutor(registry)

    assert executor.verify("unverified", {}, {"status": "ok"}) is False
    assert executor.verify("unverified", {}, {"success": True}) is False
