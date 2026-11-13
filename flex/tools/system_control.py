from __future__ import annotations

import ctypes
import os
import subprocess
from typing import Literal

from pydantic import BaseModel, Field

from flex.security.permissions import PermissionLevel
from flex.tools.registry import ToolSpec

_VK_LWIN = 0x5B
_VK_S = 0x53
_KEYEVENTF_KEYUP = 0x0002


class OpenWindowsUiArgs(BaseModel):
    action: Literal["start_menu", "windows_search"] = Field(
        ...,
        description="Open the Windows Start menu or Windows Search.",
    )


class WindowsPowerActionArgs(BaseModel):
    action: Literal["lock", "sleep", "sign_out", "restart", "shutdown"] = Field(
        ...,
        description="Windows session or power action to perform.",
    )


def _require_windows() -> None:
    if os.name != "nt":
        raise OSError("Windows system controls are only supported on Windows.")


def _send_windows_shortcut(keys: tuple[int, ...]) -> None:
    _require_windows()
    user32 = ctypes.windll.user32
    for key in keys:
        user32.keybd_event(key, 0, 0, 0)
    for key in reversed(keys):
        user32.keybd_event(key, 0, _KEYEVENTF_KEYUP, 0)


def open_windows_ui_executor(action: str) -> dict[str, str]:
    shortcuts = {
        "start_menu": (_VK_LWIN,),
        "windows_search": (_VK_LWIN, _VK_S),
    }
    try:
        shortcut = shortcuts[action]
    except KeyError as exc:
        raise ValueError(f"Unsupported Windows interface action: {action}") from exc

    _send_windows_shortcut(shortcut)
    return {"status": "opened", "action": action}


def windows_power_action_executor(action: str) -> dict[str, str | int]:
    _require_windows()

    if action == "lock":
        if not ctypes.windll.user32.LockWorkStation():
            raise ctypes.WinError(ctypes.get_last_error())
        return {"status": "started", "action": action}

    if action == "sleep":
        power_api = ctypes.WinDLL("powrprof", use_last_error=True)
        set_suspend_state = power_api.SetSuspendState
        set_suspend_state.argtypes = [ctypes.c_bool, ctypes.c_bool, ctypes.c_bool]
        set_suspend_state.restype = ctypes.c_bool
        if not set_suspend_state(False, False, False):
            raise ctypes.WinError(ctypes.get_last_error())
        return {"status": "started", "action": action}

    commands = {
        "sign_out": ["shutdown.exe", "/l"],
        "restart": ["shutdown.exe", "/r", "/t", "0"],
        "shutdown": ["shutdown.exe", "/s", "/t", "0"],
    }
    try:
        command = commands[action]
    except KeyError as exc:
        raise ValueError(f"Unsupported Windows power action: {action}") from exc

    process = subprocess.Popen(command, shell=False)
    return {"status": "started", "action": action, "process_id": process.pid}


def _open_windows_ui_verifier(_arguments: dict, result: dict) -> bool:
    return result.get("status") == "opened"


def _windows_power_action_verifier(arguments: dict, result: dict) -> bool:
    return result.get("status") == "started" and result.get("action") == arguments.get("action")


OPEN_WINDOWS_UI_TOOL = ToolSpec(
    name="open_windows_ui",
    description="Open the Windows Start menu or Windows Search.",
    schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start_menu", "windows_search"],
                "description": "Open the Start menu or Windows Search.",
            }
        },
        "required": ["action"],
        "additionalProperties": False,
    },
    permission_level=PermissionLevel.LOW_RISK,
    executor=open_windows_ui_executor,
    argument_model=OpenWindowsUiArgs,
    verification=_open_windows_ui_verifier,
)

WINDOWS_POWER_ACTION_TOOL = ToolSpec(
    name="windows_power_action",
    description=(
        "Lock or put Windows to sleep, sign out, restart, or shut down. "
        "Always request user confirmation before executing this tool."
    ),
    schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["lock", "sleep", "sign_out", "restart", "shutdown"],
                "description": "The Windows session or power action.",
            }
        },
        "required": ["action"],
        "additionalProperties": False,
    },
    permission_level=PermissionLevel.HIGH_RISK,
    executor=windows_power_action_executor,
    argument_model=WindowsPowerActionArgs,
    verification=_windows_power_action_verifier,
)
