from __future__ import annotations

from flex.security.permissions import PermissionLevel
from flex.tools.app_control import OPEN_APPLICATION_TOOL
from flex.tools.registry import ToolRegistry


def test_tool_registry_registers_tools():
    registry = ToolRegistry()
    registry.register(OPEN_APPLICATION_TOOL)

    assert "open_application" in registry
    assert registry.get("open_application").permission_level == PermissionLevel.LOW_RISK


def test_tool_registry_exposes_openai_schema():
    registry = ToolRegistry()
    registry.register(OPEN_APPLICATION_TOOL)
    tools = registry.as_openai_tools()
    assert tools[0]["function"]["name"] == "open_application"


def test_weather_application_alias_resolves_installed_windows_weather_app(monkeypatch):
    from flex.tools import app_control

    monkeypatch.setattr(app_control, "_start_menu_shortcut", lambda _application: None)
    monkeypatch.setattr(
        app_control,
        "_windows_start_apps",
        lambda: (("MSN Weather", "Microsoft.BingWeather_8wekyb3d8bbwe!App"),),
    )

    assert app_control._lookup_application("Weather") == (
        "shell:AppsFolder\\Microsoft.BingWeather_8wekyb3d8bbwe!App"
    )
