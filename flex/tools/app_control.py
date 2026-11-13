from __future__ import annotations

import csv
import io
import logging
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from flex.security.permissions import PermissionLevel
from flex.tools.registry import ToolSpec

logger = logging.getLogger("flex.tools.app_control")


class OpenApplicationArgs(BaseModel):
    application: str = Field(..., description="Application name or executable path to open.")


class CloseApplicationArgs(BaseModel):
    application: str = Field(..., description="Application name or executable path to close.")


class FindApplicationArgs(BaseModel):
    application: str = Field(..., description="Application name to search for.")


class ListRunningApplicationsArgs(BaseModel):
    pass


def _normalize_application_name(application: str) -> str:
    return "".join(character for character in application.casefold() if character.isalnum())


@lru_cache(maxsize=1)
def _windows_start_apps() -> tuple[tuple[str, str], ...]:
    if os.name != "nt":
        return ()

    command = (
        'Get-StartApps | ForEach-Object { "$($_.Name)`t$($_.AppID)" }'
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            check=True,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("Could not enumerate Windows Start apps: %s", exc)
        return ()

    entries = []
    for line in result.stdout.splitlines():
        name, separator, app_id = line.partition("\t")
        if separator and name.strip() and app_id.strip():
            entries.append((name.strip(), app_id.strip()))
    return tuple(entries)


def _start_menu_shortcut(application: str) -> str | None:
    normalized = _normalize_application_name(application)
    if not normalized or os.name != "nt":
        return None

    start_menu = os.environ.get("APPDATA")
    common_start_menu = os.environ.get("PROGRAMDATA")
    roots = [
        Path(start_menu) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        for start_menu in (start_menu, common_start_menu)
        if start_menu
    ]
    for root in roots:
        if not root.is_dir():
            continue
        for shortcut in root.rglob("*.lnk"):
            if _normalize_application_name(shortcut.stem) == normalized:
                return str(shortcut)
    return None


def _lookup_application(application: str) -> str | None:
    if not application or not application.strip():
        return None

    candidate = application.strip()
    if os.path.exists(candidate):
        return candidate

    exact = shutil.which(candidate)
    if exact:
        return exact

    aliases = {
        "settings": "ms-settings:",
        "file explorer": "explorer.exe",
        "google chrome": "chrome.exe",
        "microsoft edge": "msedge.exe",
        "vs code": "code.exe",
        "visual studio code": "code.exe",
        "windows camera": "camera",
        "windows terminal": "wt.exe",
    }
    lowered = candidate.lower()
    if lowered in aliases:
        candidate = aliases[lowered]
        lowered = candidate.lower()
        if candidate.startswith("ms-"):
            return candidate

    executable = {
        "notepad": "notepad.exe",
        "chrome": "chrome.exe",
        "edge": "msedge.exe",
        "code": "code.exe",
        "vs code": "code.exe",
        "vscode": "code.exe",
        "visual studio code": "code.exe",
        "explorer": "explorer.exe",
        "file explorer": "explorer.exe",
        "powershell": "powershell.exe",
        "camera": "WindowsCamera.exe",
        "onenote": "onenote.exe",
        "copilot": "Microsoft.Copilot.exe",
        "terminal": "wt.exe",
    }.get(lowered, candidate)

    resolved = shutil.which(executable)
    if resolved:
        return resolved

    install_roots = tuple(
        Path(root)
        for root in (
            os.environ.get("LOCALAPPDATA"),
            os.environ.get("PROGRAMFILES"),
            os.environ.get("PROGRAMFILES(X86)"),
        )
        if root
    )
    install_locations = {
        "chrome.exe": (
            Path("Google") / "Chrome" / "Application" / "chrome.exe",
        ),
        "msedge.exe": (
            Path("Microsoft") / "Edge" / "Application" / "msedge.exe",
        ),
        "code.exe": (
            Path("Programs") / "Microsoft VS Code" / "Code.exe",
            Path("Microsoft VS Code") / "Code.exe",
        ),
    }
    for relative_path in install_locations.get(Path(executable).name.lower(), ()):
        for root in install_roots:
            path = root / relative_path
            if path.is_file():
                return str(path)

    shortcut = _start_menu_shortcut(application)
    if shortcut:
        return shortcut

    normalized = _normalize_application_name(application)
    start_app_aliases = {
        "camera": {"windowscamera"},
        "copilot": {"microsoftcopilot"},
        "weather": {"msnweather", "microsoftweather", "bingweather"},
        "windowsweather": {"weather", "msnweather", "microsoftweather", "bingweather"},
        "microsoftweather": {"weather", "msnweather", "bingweather"},
    }
    start_apps = _windows_start_apps()
    exact_names = {normalized} | start_app_aliases.get(normalized, set())
    for app_name, app_id in start_apps:
        if _normalize_application_name(app_name) in exact_names:
            return f"shell:AppsFolder\\{app_id}"
    partial_matches = [
        app_id
        for app_name, app_id in start_apps
        if normalized and normalized in _normalize_application_name(app_name)
    ]
    if len(partial_matches) == 1:
        return f"shell:AppsFolder\\{partial_matches[0]}"

    return None


def open_application_executor(application: str) -> dict[str, object]:
    resolved = _lookup_application(application)
    if not resolved:
        raise FileNotFoundError(f"Application '{application}' was not found.")

    if resolved.startswith(("ms-", "shell:AppsFolder\\")) or resolved.lower().endswith(".lnk"):
        os.startfile(resolved)
    else:
        process = subprocess.Popen([resolved], shell=False)
        return {
            "status": "opened",
            "application": application,
            "path": resolved,
            "process_id": process.pid,
        }
    return {"status": "opened", "application": application, "path": resolved}


def close_application_executor(application: str) -> dict[str, object]:
    resolved = _lookup_application(application)
    if not resolved:
        raise FileNotFoundError(f"Application '{application}' was not found.")

    try:
        subprocess.run(["taskkill", "/F", "/IM", Path(resolved).name], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Could not close '{application}': {exc.stderr}") from exc

    return {"status": "closed", "application": application, "path": resolved}


def find_application_executor(application: str) -> dict[str, object]:
    resolved = _lookup_application(application)
    if not resolved:
        return {"status": "not_found", "application": application}
    return {"status": "found", "application": application, "path": resolved}


def list_running_applications_executor() -> dict[str, object]:
    result = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, check=True)
    rows = []
    for parts in csv.reader(io.StringIO(result.stdout)):
        if parts and parts[0]:
            rows.append(parts[0])
    return {"status": "ok", "applications": rows}


def open_application_verifier(
    arguments: dict[str, Any],
    result: dict[str, Any],
) -> bool:
    resolved = result.get("path") or _lookup_application(str(arguments.get("application", "")))
    return bool(resolved and result.get("status") == "opened")


def close_application_verifier(
    _arguments: dict[str, Any],
    result: dict[str, Any],
) -> bool:
    if result.get("status") != "closed" or not result.get("path"):
        return False
    process_name = Path(str(result["path"])).name
    try:
        check = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        logger.exception("Could not verify that application %s closed.", process_name)
        return False
    return not any(
        row and row[0].casefold() == process_name.casefold()
        for row in csv.reader(io.StringIO(check.stdout))
    )


def find_application_verifier(
    arguments: dict[str, Any],
    result: dict[str, Any],
) -> bool:
    resolved = _lookup_application(str(arguments.get("application", "")))
    if result.get("status") == "not_found":
        return resolved is None
    return result.get("status") == "found" and resolved == result.get("path")


def list_running_applications_verifier(
    _arguments: dict[str, Any],
    result: dict[str, Any],
) -> bool:
    applications = result.get("applications")
    return (
        result.get("status") == "ok"
        and isinstance(applications, list)
        and all(isinstance(application, str) for application in applications)
    )


OPEN_APPLICATION_TOOL = ToolSpec(
    name="open_application",
    description="Open a known Windows application or executable.",
    schema={
        "type": "object",
        "properties": {"application": {"type": "string", "description": "Application name or path."}},
        "required": ["application"],
    },
    permission_level=PermissionLevel.LOW_RISK,
    executor=open_application_executor,
    argument_model=OpenApplicationArgs,
    verification=open_application_verifier,
)

CLOSE_APPLICATION_TOOL = ToolSpec(
    name="close_application",
    description="Close a running Windows application.",
    schema={
        "type": "object",
        "properties": {"application": {"type": "string", "description": "Application name to close."}},
        "required": ["application"],
    },
    permission_level=PermissionLevel.HIGH_RISK,
    executor=close_application_executor,
    argument_model=CloseApplicationArgs,
    verification=close_application_verifier,
)

FIND_APPLICATION_TOOL = ToolSpec(
    name="find_application",
    description="Locate an application on disk or in PATH.",
    schema={
        "type": "object",
        "properties": {"application": {"type": "string", "description": "Application name to locate."}},
        "required": ["application"],
    },
    permission_level=PermissionLevel.READ_ONLY,
    executor=find_application_executor,
    argument_model=FindApplicationArgs,
    verification=find_application_verifier,
)

LIST_RUNNING_APPLICATIONS_TOOL = ToolSpec(
    name="list_running_applications",
    description="List currently running applications.",
    schema={"type": "object", "properties": {}},
    permission_level=PermissionLevel.READ_ONLY,
    executor=list_running_applications_executor,
    argument_model=ListRunningApplicationsArgs,
    verification=list_running_applications_verifier,
)
