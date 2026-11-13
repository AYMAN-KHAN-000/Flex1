from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from flex.security.permissions import PermissionLevel
from flex.security.validator import ToolValidationError
from flex.tools.registry import ToolSpec


class CreateFolderArgs(BaseModel):
    path: str = Field(..., description="Folder path to create.")


class ListDirectoryArgs(BaseModel):
    path: str = Field(..., description="Directory to inspect.")


class SearchFilesArgs(BaseModel):
    path: str = Field(..., description="Base directory to search.")
    pattern: str = Field("*", description="Filename pattern to match.")


class CreateFileArgs(BaseModel):
    path: str = Field(..., description="File path to create within the current user's profile.")
    content: str = Field("", description="Optional text to write into the new file.")


class OpenFileArgs(BaseModel):
    path: str = Field(..., description="Existing file path within the current user's profile.")
    application: str = Field(..., description="Application to open the file with, such as VS Code.")


def _safe_directory(path: str) -> Path:
    cleaned = os.path.expandvars(os.path.expanduser(path.strip()))
    if not cleaned:
        raise ToolValidationError("A non-empty path is required.")

    supplied = Path(cleaned)
    home = Path.home().resolve()
    user_folders = {
        "desktop": _known_user_folder("Desktop"),
        "downloads": _known_user_folder("Downloads"),
        "documents": _known_user_folder("Documents"),
    }

    first_part = supplied.parts[0].rstrip(":").lower() if supplied.parts else ""
    if first_part in user_folders:
        candidate = user_folders[first_part].joinpath(*supplied.parts[1:])
    elif supplied.is_absolute():
        candidate = supplied
    else:
        candidate = Path.cwd() / supplied

    candidate = candidate.resolve(strict=False)
    try:
        candidate.relative_to(home)
    except ValueError as exc:
        raise ToolValidationError("Filesystem access is limited to the current user's profile.") from exc
    return candidate


def _known_user_folder(name: str) -> Path:
    home = Path.home()
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as key:
                value, _ = winreg.QueryValueEx(key, name)
            folder = Path(os.path.expandvars(value)).resolve(strict=False)
            try:
                folder.relative_to(home.resolve())
            except ValueError:
                pass
            else:
                return folder
        except (ImportError, OSError):
            pass

    one_drive = os.environ.get("OneDrive")
    if one_drive:
        synced_folder = Path(one_drive) / name
        if synced_folder.is_dir():
            return synced_folder.resolve()
    return home / name


def create_folder_executor(path: str) -> dict[str, object]:
    target = _safe_directory(path)
    target.mkdir(parents=True, exist_ok=True)
    return {"status": "created", "path": str(target)}


def list_directory_executor(path: str) -> dict[str, object]:
    target = _safe_directory(path)
    if not target.exists() or not target.is_dir():
        raise FileNotFoundError(f"Directory '{path}' does not exist.")
    return {"status": "ok", "path": str(target), "entries": [entry.name for entry in sorted(target.iterdir())]}


def search_files_executor(path: str, pattern: str = "*") -> dict[str, object]:
    target = _safe_directory(path)
    if not target.exists() or not target.is_dir():
        raise FileNotFoundError(f"Directory '{path}' does not exist.")
    matches: list[str] = []
    inaccessible: list[str] = []
    visited = 0
    capped = False

    def record_error(error: OSError) -> None:
        if error.filename:
            inaccessible.append(error.filename)

    for current, directories, filenames in os.walk(target, onerror=record_error):
        visited += 1
        if visited > 20_000:
            capped = True
            break
        directories[:] = [
            name
            for name in directories
            if name.casefold() not in {"$recycle.bin", "system volume information"}
        ]
        for filename in filenames:
            candidate = Path(current) / filename
            if candidate.match(pattern):
                matches.append(str(candidate))
                if len(matches) > 10:
                    capped = True
                    break
        if capped:
            break

    return {
        "status": "ok",
        "path": str(target),
        "matches": matches[:10],
        "truncated": capped,
        "inaccessible_locations": inaccessible[:20],
        "inaccessible_count": len(inaccessible),
    }


def create_file_executor(path: str, content: str = "") -> dict[str, object]:
    target = _safe_directory(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8", newline="") as file:
        file.write(content)
    return {"status": "created", "path": str(target), "characters": len(content)}


def create_file_verifier(arguments: dict[str, Any], result: dict[str, Any]) -> bool:
    target = _safe_directory(str(arguments.get("path", "")))
    if (
        result.get("status") != "created"
        or Path(str(result.get("path", ""))) != target
        or not target.is_file()
    ):
        return False
    with target.open("r", encoding="utf-8", newline="") as file:
        content = file.read()
    return content == str(arguments.get("content", ""))


def open_file_executor(path: str, application: str) -> dict[str, object]:
    from flex.tools.app_control import _lookup_application

    target = _safe_directory(path)
    if not target.is_file():
        raise FileNotFoundError(f"File '{path}' does not exist.")
    resolved = _lookup_application(application)
    if not resolved:
        raise FileNotFoundError(f"Application '{application}' was not found.")
    if resolved.startswith(("ms-", "shell:AppsFolder\\")) or resolved.lower().endswith(".lnk"):
        raise RuntimeError(f"Application '{application}' cannot open a file path.")

    process = subprocess.Popen([resolved, str(target)], shell=False)
    return {
        "status": "opened",
        "path": str(target),
        "application": application,
        "process_id": process.pid,
    }


def open_file_verifier(_arguments: dict[str, Any], result: dict[str, Any]) -> bool:
    target = Path(str(result.get("path", "")))
    return (
        result.get("status") == "opened"
        and target.is_file()
        and isinstance(result.get("process_id"), int)
    )


def create_folder_verifier(
    arguments: dict[str, Any],
    result: dict[str, Any],
) -> bool:
    target = Path(str(result.get("path", "")))
    return (
        result.get("status") == "created"
        and target == _safe_directory(str(arguments.get("path", "")))
        and target.is_dir()
    )


def list_directory_verifier(
    arguments: dict[str, Any],
    result: dict[str, Any],
) -> bool:
    target = _safe_directory(str(arguments.get("path", "")))
    entries = result.get("entries")
    if result.get("status") != "ok" or result.get("path") != str(target):
        return False
    if not isinstance(entries, list) or not all(isinstance(entry, str) for entry in entries):
        return False
    return target.is_dir() and entries == sorted(entry.name for entry in target.iterdir())


def search_files_verifier(
    arguments: dict[str, Any],
    result: dict[str, Any],
) -> bool:
    target = _safe_directory(str(arguments.get("path", "")))
    pattern = str(arguments.get("pattern", "*"))
    matches = result.get("matches")
    if result.get("status") != "ok" or result.get("path") != str(target):
        return False
    if not isinstance(matches, list) or not all(isinstance(match, str) for match in matches):
        return False
    try:
        return target.is_dir() and all(
            (candidate := Path(match)).is_file()
            and candidate.is_relative_to(target)
            and candidate.match(pattern)
            for match in matches
        )
    except (OSError, ValueError):
        return False


CREATE_FOLDER_TOOL = ToolSpec(
    name="create_folder",
    description="Create a folder safely without allowing path traversal.",
    schema={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Target folder path."}},
        "required": ["path"],
    },
    permission_level=PermissionLevel.MEDIUM_RISK,
    executor=create_folder_executor,
    argument_model=CreateFolderArgs,
    verification=create_folder_verifier,
)

CREATE_FILE_TOOL = ToolSpec(
    name="create_file",
    description=(
        "Create a new text file within the current user's profile and optionally write text into it. "
        "Does not overwrite an existing file."
    ),
    schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Target file path."},
            "content": {"type": "string", "description": "Optional text for the new file."},
        },
        "required": ["path"],
    },
    permission_level=PermissionLevel.MEDIUM_RISK,
    executor=create_file_executor,
    argument_model=CreateFileArgs,
    verification=create_file_verifier,
)

OPEN_FILE_TOOL = ToolSpec(
    name="open_file",
    description="Open an existing file from the current user's profile in a named application.",
    schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Existing file path."},
            "application": {"type": "string", "description": "Application to open the file with."},
        },
        "required": ["path", "application"],
    },
    permission_level=PermissionLevel.LOW_RISK,
    executor=open_file_executor,
    argument_model=OpenFileArgs,
    verification=open_file_verifier,
)

LIST_DIRECTORY_TOOL = ToolSpec(
    name="list_directory",
    description="List files and folders in a target directory.",
    schema={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Directory to inspect."}},
        "required": ["path"],
    },
    permission_level=PermissionLevel.READ_ONLY,
    executor=list_directory_executor,
    argument_model=ListDirectoryArgs,
    verification=list_directory_verifier,
)

SEARCH_FILES_TOOL = ToolSpec(
    name="search_files",
    description="Search a directory tree for files matching a pattern.",
    schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Base directory to search."},
            "pattern": {"type": "string", "description": "Glob pattern to match, for example '*.txt'."},
        },
        "required": ["path"],
    },
    permission_level=PermissionLevel.READ_ONLY,
    executor=search_files_executor,
    argument_model=SearchFilesArgs,
    verification=search_files_verifier,
)
