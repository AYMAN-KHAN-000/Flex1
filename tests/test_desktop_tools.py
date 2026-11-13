from __future__ import annotations

from types import SimpleNamespace

from flex.core.agent import FLEXAgent
from flex.security.permissions import PermissionLevel, PermissionManager
from flex.tools import app_control, desktop
from flex.tools.app_control import OpenApplicationArgs
from flex.tools.desktop import TypeTextArgs
from flex.tools.registry import ToolRegistry, ToolSpec


def test_application_lookup_resolves_edge_from_common_install_locations(tmp_path, monkeypatch):
    edge = tmp_path / "Microsoft" / "Edge" / "Application" / "msedge.exe"
    edge.parent.mkdir(parents=True)
    edge.touch()
    monkeypatch.setattr(app_control.shutil, "which", lambda _executable: None)
    monkeypatch.setattr(app_control, "_start_menu_shortcut", lambda _application: None)
    monkeypatch.setattr(app_control, "_windows_start_apps", lambda: ())
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("PROGRAMFILES", raising=False)
    monkeypatch.delenv("PROGRAMFILES(X86)", raising=False)

    assert app_control._lookup_application("edge") == str(edge)


def test_application_lookup_finds_registered_start_app(monkeypatch):
    monkeypatch.setattr(app_control.shutil, "which", lambda _executable: None)
    monkeypatch.setattr(app_control, "_start_menu_shortcut", lambda _application: None)
    monkeypatch.setattr(
        app_control,
        "_windows_start_apps",
        lambda: (("Windows Camera", "Microsoft.WindowsCamera_8wekyb3d8bbwe!App"),),
    )

    assert app_control._lookup_application("camera") == (
        "shell:AppsFolder\\Microsoft.WindowsCamera_8wekyb3d8bbwe!App"
    )


def test_open_application_verification_checks_successful_dispatch():
    result = {"status": "opened", "application": "camera", "path": "shell:AppsFolder\\camera!App"}

    assert app_control.open_application_verifier({"application": "camera"}, result) is True
    assert app_control.open_application_verifier(
        {"application": "camera"},
        {"status": "failed", "path": "shell:AppsFolder\\camera!App"},
    ) is False


def test_open_application_launches_and_verifies_without_tasklist_race(monkeypatch):
    monkeypatch.setattr(app_control, "_lookup_application", lambda _application: "C:\\Windows\\notepad.exe")
    monkeypatch.setattr(
        app_control.subprocess,
        "Popen",
        lambda args, **kwargs: SimpleNamespace(pid=321),
    )

    result = app_control.open_application_executor("notepad")

    assert result["process_id"] == 321
    assert app_control.open_application_verifier({"application": "notepad"}, result) is True


def test_search_web_opens_query_in_requested_browser(monkeypatch):
    calls = []
    monkeypatch.setattr(desktop, "_lookup_application", lambda browser: f"C:\\Apps\\{browser}.exe")
    monkeypatch.setattr(
        desktop.subprocess,
        "Popen",
        lambda args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(pid=45),
    )

    result = desktop.search_web_executor("microsoft copilot", "chrome")

    assert calls == [
        (
            [
                "C:\\Apps\\chrome.exe",
                "https://www.google.com/search?q=microsoft+copilot",
            ],
            {"shell": False},
        )
    ]
    assert result["status"] == "opened"
    assert desktop._search_web_verifier({}, result) is True


def test_search_web_uses_default_browser(monkeypatch):
    calls = []
    monkeypatch.setattr(desktop.webbrowser, "open", lambda url, new: calls.append((url, new)) or True)

    result = desktop.search_web_executor("hello world")

    assert calls == [("https://www.google.com/search?q=hello+world", 2)]
    assert result["browser"] == "default"


def test_type_text_targets_named_application(monkeypatch):
    calls = []
    monkeypatch.setattr(
        desktop,
        "_type_text_into_application",
        lambda application, text: calls.append((application, text))
        or {"status": "typed", "application": application, "characters": len(text)},
    )

    result = desktop.type_text_executor("notepad", "hello")

    assert calls == [("notepad", "hello")]
    assert desktop._type_text_verifier({}, result) is True


def test_agent_executes_open_then_type_tool_calls_in_order():
    actions = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="open_application",
            description="Open an application.",
            schema=app_control.OPEN_APPLICATION_TOOL.schema,
            permission_level=PermissionLevel.LOW_RISK,
            executor=lambda application: actions.append(("open", application))
            or {"status": "opened"},
            argument_model=OpenApplicationArgs,
            verification=lambda _arguments, result: result.get("status") == "opened",
        )
    )
    registry.register(
        ToolSpec(
            name="type_text",
            description="Type text into an application.",
            schema=desktop.TYPE_TEXT_TOOL.schema,
            permission_level=PermissionLevel.LOW_RISK,
            executor=lambda application, text: actions.append(("type", application, text))
            or {"status": "typed"},
            argument_model=TypeTextArgs,
            verification=lambda _arguments, result: result.get("status") == "typed",
        )
    )

    class Provider:
        def __init__(self):
            self.responses = [
                SimpleNamespace(
                    has_tool_calls=True,
                    tool_calls=[
                        {"tool": "open_application", "arguments": {"application": "notepad"}},
                        {
                            "tool": "type_text",
                            "arguments": {"application": "notepad", "text": "hello"},
                        },
                    ],
                    content="",
                ),
                SimpleNamespace(has_tool_calls=False, tool_calls=[], content=""),
            ]

        def chat(self, _messages, *, tools=None):
            return self.responses.pop(0)

    agent = FLEXAgent(
        provider=Provider(),
        registry=registry,
        permission_manager=PermissionManager(require_confirmation=False),
    )

    assert agent.process_request("Open Notepad and write hello") == "Done."
    assert actions == [("open", "notepad"), ("type", "notepad", "hello")]
