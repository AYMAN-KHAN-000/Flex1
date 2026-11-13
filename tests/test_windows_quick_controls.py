from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from flex.security.permissions import PermissionLevel
from flex.tools import windows_quick_controls
from flex.tools.windows_quick_controls import create_windows_quick_control_tools


def test_wifi_control_toggles_and_verifies_selected_physical_adapters(monkeypatch):
    states = [{"Name": "Wi-Fi", "Status": "Disabled"}]
    commands = []

    def run(script):
        commands.append(script)
        if "ConvertTo-Json" in script:
            return json.dumps(states)
        states[0]["Status"] = "Up"
        return ""

    monkeypatch.setattr(windows_quick_controls, "_run_powershell", run)

    result = windows_quick_controls.wifi_control_executor("on")

    assert result["success"] is True
    assert result["enabled"] is True
    assert "Enable-NetAdapter" in commands[1]
    assert windows_quick_controls._wifi_verifier({"operation": "on"}, result)


def test_wifi_missing_adapter_reports_hardware_unavailable(monkeypatch):
    monkeypatch.setattr(windows_quick_controls, "_run_powershell", lambda _script: "")

    with pytest.raises(RuntimeError, match="No physical Wi-Fi adapter"):
        windows_quick_controls.wifi_control_executor("status")


@pytest.mark.parametrize(
    ("executor", "control", "uri"),
    [
        (windows_quick_controls.bluetooth_control_executor, "bluetooth", "ms-settings:bluetooth"),
        (windows_quick_controls.night_light_control_executor, "night_light", "ms-settings:nightlight"),
        (
            windows_quick_controls.airplane_mode_control_executor,
            "airplane_mode",
            "ms-settings:network-airplanemode",
        ),
    ],
)
def test_unsupported_controls_open_settings_without_claiming_state_changed(
    monkeypatch,
    executor,
    control,
    uri,
):
    opened = []
    monkeypatch.setattr(windows_quick_controls.webbrowser, "open", lambda target, new: opened.append((target, new)) or True)

    result = executor("on")

    assert opened == [(uri, 0)]
    assert result["control"] == control
    assert result["state_changed"] is False
    assert result["requested_operation"] == "on"


def test_unsupported_control_status_does_not_fabricate_state(monkeypatch):
    monkeypatch.setattr(windows_quick_controls.webbrowser, "open", lambda *_args, **_kwargs: True)

    with pytest.raises(RuntimeError, match="does not expose a reliable"):
        windows_quick_controls.bluetooth_control_executor("status")


def test_airplane_mode_always_requires_high_risk_confirmation():
    tools = {tool.name: tool for tool in create_windows_quick_control_tools()}

    assert tools["airplane_mode_control"].permission_level == PermissionLevel.HIGH_RISK
    assert tools["wifi_control"].permission_level == PermissionLevel.LOW_RISK
