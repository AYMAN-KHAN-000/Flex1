from __future__ import annotations

from types import SimpleNamespace

import pytest

from flex.security.permissions import PermissionLevel, PermissionManager
from flex.tools import system_control
from flex.tools.system_control import (
    OPEN_WINDOWS_UI_TOOL,
    WINDOWS_POWER_ACTION_TOOL,
    open_windows_ui_executor,
    windows_power_action_executor,
)


def test_open_windows_ui_sends_start_and_search_shortcuts(monkeypatch):
    shortcuts = []
    monkeypatch.setattr(system_control, "_send_windows_shortcut", shortcuts.append)

    assert open_windows_ui_executor("start_menu") == {"status": "opened", "action": "start_menu"}
    assert open_windows_ui_executor("windows_search") == {
        "status": "opened",
        "action": "windows_search",
    }
    assert shortcuts == [(system_control._VK_LWIN,), (system_control._VK_LWIN, system_control._VK_S)]


@pytest.mark.parametrize(
    ("action", "expected_command"),
    [
        ("sign_out", ["shutdown.exe", "/l"]),
        ("restart", ["shutdown.exe", "/r", "/t", "0"]),
        ("shutdown", ["shutdown.exe", "/s", "/t", "0"]),
    ],
)
def test_windows_power_action_uses_fixed_command(action, expected_command, monkeypatch):
    commands = []
    monkeypatch.setattr(system_control, "_require_windows", lambda: None)
    monkeypatch.setattr(
        system_control.subprocess,
        "Popen",
        lambda command, **kwargs: commands.append((command, kwargs)) or SimpleNamespace(pid=123),
    )

    result = windows_power_action_executor(action)

    assert commands == [(expected_command, {"shell": False})]
    assert result == {"status": "started", "action": action, "process_id": 123}
    assert system_control._windows_power_action_verifier({"action": action}, result)


def test_system_control_tool_risk_levels_are_explicit():
    assert OPEN_WINDOWS_UI_TOOL.permission_level == PermissionLevel.LOW_RISK
    assert WINDOWS_POWER_ACTION_TOOL.permission_level == PermissionLevel.HIGH_RISK


def test_high_risk_action_requires_callback_approval():
    manager = PermissionManager()
    denied = manager.can_execute("windows_power_action", PermissionLevel.HIGH_RISK)
    assert denied.allowed is False

    manager = PermissionManager(confirmation_callback=lambda _name, details: details == '{"action": "lock"}')
    approved = manager.can_execute(
        "windows_power_action",
        PermissionLevel.HIGH_RISK,
        '{"action": "lock"}',
    )
    assert approved.allowed is True
