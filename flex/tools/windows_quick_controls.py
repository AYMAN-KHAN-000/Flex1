from __future__ import annotations

import json
import os
import subprocess
import webbrowser
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from flex.security.permissions import PermissionLevel
from flex.tools.registry import ToolSpec

_SETTINGS_URIS = {
    "wifi": "ms-settings:network-wifi",
    "bluetooth": "ms-settings:bluetooth",
    "display": "ms-settings:display",
    "night_light": "ms-settings:nightlight",
    "airplane_mode": "ms-settings:network-airplanemode",
}

_POWERSHELL_WIFI_QUERY = (
    "Get-NetAdapter -Physical | Where-Object { $_.NdisPhysicalMedium -eq 9 } "
    "| Select-Object Name,Status | ConvertTo-Json -Compress"
)


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpenWindowsSettingsArgs(StrictArgs):
    page: Literal["wifi", "bluetooth", "display", "night_light", "airplane_mode"]


class WifiControlArgs(StrictArgs):
    operation: Literal["status", "on", "off"]


class BluetoothControlArgs(StrictArgs):
    operation: Literal["status", "on", "off"]


class NightLightControlArgs(StrictArgs):
    operation: Literal["status", "on", "off"]


class AirplaneModeControlArgs(StrictArgs):
    operation: Literal["status", "on", "off"]


def _require_windows() -> None:
    if os.name != "nt":
        raise OSError("Windows quick controls are only supported on Windows.")


def _run_powershell(script: str) -> str:
    _require_windows()
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
        shell=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "Windows PowerShell returned an error."
        raise RuntimeError(detail)
    return completed.stdout.strip()


def _wifi_adapters() -> list[dict[str, str]]:
    output = _run_powershell(_POWERSHELL_WIFI_QUERY)
    if not output:
        return []
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Windows returned an invalid Wi-Fi adapter status.") from exc
    devices = parsed if isinstance(parsed, list) else [parsed]
    return [
        {"name": str(device["Name"]), "status": str(device["Status"])}
        for device in devices
        if isinstance(device, dict) and device.get("Name") and device.get("Status")
    ]


def wifi_control_executor(operation: str) -> dict[str, Any]:
    adapters = _wifi_adapters()
    if not adapters:
        raise RuntimeError("No physical Wi-Fi adapter is available.")

    target_status = {"on": "Up", "off": "Disabled"}.get(operation)
    if target_status is not None:
        action = "Enable" if operation == "on" else "Disable"
        # Adapter names are selected from Windows itself and passed as escaped PowerShell literals.
        names = [adapter["name"] for adapter in adapters]
        quoted_names = ",".join("'" + name.replace("'", "''") + "'" for name in names)
        script = (
            f"$names = @({quoted_names}); "
            f"Get-NetAdapter -Name $names -Physical | "
            f"Where-Object {{ $_.NdisPhysicalMedium -eq 9 }} | "
            f"{action}-NetAdapter -Confirm:$false -ErrorAction Stop"
        )
        _run_powershell(script)
        adapters = _wifi_adapters()
        if not adapters or any(adapter["status"] != target_status for adapter in adapters):
            raise RuntimeError(f"Windows did not verify Wi-Fi {operation}.")
    elif operation != "status":
        raise ValueError(f"Unsupported Wi-Fi operation: {operation}")

    enabled = any(adapter["status"].casefold() == "up" for adapter in adapters)
    return {
        "success": True,
        "enabled": enabled,
        "adapters": adapters,
        "operation": operation,
    }


def _settings_fallback(control: str, operation: str) -> dict[str, Any]:
    _require_windows()
    if operation == "status":
        raise RuntimeError(
            f"Windows does not expose a reliable {control.replace('_', ' ')} state through this control."
        )
    page = "airplane_mode" if control == "airplane_mode" else control
    uri = _SETTINGS_URIS[page]
    if not webbrowser.open(uri, new=0):
        raise RuntimeError(f"Windows could not open {control.replace('_', ' ')} Settings.")
    return {
        "success": True,
        "action": "opened_settings",
        "control": control,
        "requested_operation": operation,
        "settings_uri": uri,
        "state_changed": False,
    }


def bluetooth_control_executor(operation: str) -> dict[str, Any]:
    return _settings_fallback("bluetooth", operation)


def night_light_control_executor(operation: str) -> dict[str, Any]:
    return _settings_fallback("night_light", operation)


def airplane_mode_control_executor(operation: str) -> dict[str, Any]:
    return _settings_fallback("airplane_mode", operation)


def open_windows_settings_executor(page: str) -> dict[str, Any]:
    _require_windows()
    uri = _SETTINGS_URIS[page]
    if not webbrowser.open(uri, new=0):
        raise RuntimeError(f"Windows could not open the {page.replace('_', ' ')} settings page.")
    return {"success": True, "action": "opened_settings", "page": page, "settings_uri": uri}


def _wifi_verifier(arguments: dict[str, Any], result: dict[str, Any]) -> bool:
    if result.get("success") is not True:
        return False
    operation = arguments.get("operation")
    if operation == "on":
        return result.get("enabled") is True
    if operation == "off":
        return result.get("enabled") is False
    return isinstance(result.get("adapters"), list)


def _settings_fallback_verifier(_arguments: dict[str, Any], result: dict[str, Any]) -> bool:
    return result.get("success") is True and result.get("action") == "opened_settings"


def _register(
    name: str,
    description: str,
    args_model: type[BaseModel],
    executor: Any,
    permission: PermissionLevel,
    verifier: Any = _settings_fallback_verifier,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        schema=args_model.model_json_schema(),
        permission_level=permission,
        executor=executor,
        argument_model=args_model,
        verification=verifier,
    )


def create_windows_quick_control_tools() -> list[ToolSpec]:
    return [
        _register(
            "wifi_control",
            "Query and toggle physical Wi-Fi adapters; after a change, re-query and verify all adapters. This may require Windows administrator permissions.",
            WifiControlArgs,
            wifi_control_executor,
            PermissionLevel.LOW_RISK,
            _wifi_verifier,
        ),
        _register(
            "bluetooth_control",
            "Bluetooth radio status/toggling is not directly exposed by this Windows integration. Open Bluetooth Settings for the user to inspect or change it; never claim the state changed.",
            BluetoothControlArgs,
            bluetooth_control_executor,
            PermissionLevel.LOW_RISK,
        ),
        _register(
            "night_light_control",
            "Night Light status/toggling is not reliably exposed by Windows on all systems. Open Night Light Settings for the user; never claim the state changed.",
            NightLightControlArgs,
            night_light_control_executor,
            PermissionLevel.LOW_RISK,
        ),
        _register(
            "airplane_mode_control",
            "Airplane Mode is disruptive. With explicit confirmation, open Windows Airplane Mode Settings. This integration does not directly change the state.",
            AirplaneModeControlArgs,
            airplane_mode_control_executor,
            PermissionLevel.HIGH_RISK,
        ),
        _register(
            "open_windows_settings",
            "Open Windows Settings to Wi-Fi, Bluetooth, display, Night Light, or Airplane Mode.",
            OpenWindowsSettingsArgs,
            open_windows_settings_executor,
            PermissionLevel.LOW_RISK,
        ),
    ]
