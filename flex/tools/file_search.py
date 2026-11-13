from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from flex.security.permissions import PermissionLevel
from flex.tools.filesystem import _known_user_folder
from flex.tools.registry import ToolSpec

MAX_SEARCH_RESULTS = 10
MAX_VISITED_DIRECTORIES = 20_000

_FILE_TYPES = {
    "image": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"},
    "images": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"},
    "pdf": {".pdf"},
    "document": {".doc", ".docx", ".odt", ".rtf", ".txt", ".pdf"},
    "documents": {".doc", ".docx", ".odt", ".rtf", ".txt", ".pdf"},
    "video": {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".webm"},
    "videos": {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".webm"},
    "audio": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"},
    "blender": {".blend", ".blend1"},
}

_COMMON_FOLDERS = ("Desktop", "Documents", "Downloads", "Pictures", "Videos")


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FindFilesArgs(StrictArgs):
    name: str = Field(default="", description="Filename fragment or exact filename.")
    location: str | None = Field(default=None, description="Known or user-selected folder.")
    file_type: str | None = Field(default=None, description="File extension or common type, such as images or PDF.")
    latest: bool = Field(default=False, description="Return the newest matching files first.")


class FindFoldersArgs(StrictArgs):
    name: str = Field(..., min_length=1, description="Folder name or name fragment.")
    location: str | None = Field(default=None, description="Known or user-selected folder.")


class FindRecentFilesArgs(StrictArgs):
    location: str | None = Field(default=None, description="Known or user-selected folder.")
    days: int = Field(default=7, ge=1, le=3650)
    file_type: str | None = None


class FilePathArgs(StrictArgs):
    path: str = Field(..., min_length=1)


class OpenFoundFileArgs(FilePathArgs):
    application: str | None = Field(default=None, description="Optional application, such as VS Code.")


class FindFilesTools:
    def __init__(self, search_locations: list[str] | None = None) -> None:
        self.search_locations = search_locations if search_locations is not None else []
        self._inaccessible: list[str] = []
        self._scan_capped = False

    def _roots(self, location: str | None) -> list[Path]:
        if location:
            cleaned = location.strip()
            common = {
                name.casefold(): _known_user_folder(name)
                for name in (*_COMMON_FOLDERS, "OneDrive")
            }
            if os.environ.get("OneDrive"):
                common["onedrive"] = Path(os.environ["OneDrive"]).expanduser()
            for configured in self.search_locations:
                common[Path(configured).name.casefold()] = Path(configured).expanduser()
            if cleaned.casefold() in common:
                candidates = [common[cleaned.casefold()]]
            else:
                supplied = Path(os.path.expandvars(os.path.expanduser(cleaned)))
                resolved = supplied.resolve(strict=False)
                allowed_roots = [
                    *(_known_user_folder(name) for name in _COMMON_FOLDERS),
                    *(Path(folder).expanduser() for folder in self.search_locations),
                ]
                one_drive = os.environ.get("OneDrive")
                if one_drive:
                    allowed_roots.append(Path(one_drive))
                if not any(
                    resolved == allowed.resolve(strict=False)
                    or resolved.is_relative_to(allowed.resolve(strict=False))
                    for allowed in allowed_roots
                ):
                    raise ValueError(
                        "Search only accepts a common folder or a folder inside a user-selected search location."
                    )
                candidates = [supplied]
        else:
            candidates = [_known_user_folder(name) for name in _COMMON_FOLDERS]
            candidates.extend(Path(folder).expanduser() for folder in self.search_locations)
            one_drive = os.environ.get("OneDrive")
            if one_drive:
                candidates.append(Path(one_drive))

        roots: list[Path] = []
        self._inaccessible = []
        self._scan_capped = False
        seen: set[str] = set()
        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                self._inaccessible.append(str(candidate))
                continue
            if not resolved.is_dir():
                self._inaccessible.append(str(resolved))
                continue
            key = os.path.normcase(str(resolved))
            if key not in seen:
                seen.add(key)
                roots.append(resolved)
        if not roots:
            raise FileNotFoundError(
                f"No accessible search folder was found for {location!r}."
                if location
                else "No accessible common or user-selected search folders were found."
            )
        return roots

    @staticmethod
    def _extensions(file_type: str | None) -> set[str] | None:
        if not file_type:
            return None
        cleaned = file_type.strip().casefold()
        if cleaned in _FILE_TYPES:
            return _FILE_TYPES[cleaned]
        extension = cleaned if cleaned.startswith(".") else f".{cleaned}"
        if len(extension) > 16 or any(character in extension for character in "\\/*?"):
            raise ValueError(f"Unsupported file type: {file_type}")
        return {extension}

    def _walk(self, roots: list[Path]):
        visited = 0
        for root in roots:
            def record_error(error: OSError) -> None:
                if error.filename:
                    self._inaccessible.append(error.filename)

            for current, directories, files in os.walk(root, onerror=record_error):
                visited += 1
                if visited > MAX_VISITED_DIRECTORIES:
                    self._scan_capped = True
                    return
                directories[:] = [
                    name
                    for name in directories
                    if name.casefold() not in {"$recycle.bin", "system volume information"}
                ]
                yield Path(current), directories, files

    @staticmethod
    def _file_record(path: Path) -> dict[str, Any]:
        stat = path.stat()
        return {
            "path": str(path),
            "name": path.name,
            "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
        }

    def _result(self, records: list[dict[str, Any]], matches_found: int, roots: list[Path]) -> dict[str, Any]:
        return {
            "success": True,
            "results": records,
            "count": len(records),
            "truncated": matches_found > MAX_SEARCH_RESULTS or self._scan_capped,
            "locations_searched": [str(root) for root in roots],
            "inaccessible_locations": self._inaccessible[:20],
            "inaccessible_count": len(self._inaccessible),
        }

    def find_files(
        self,
        name: str = "",
        location: str | None = None,
        file_type: str | None = None,
        latest: bool = False,
    ) -> dict[str, Any]:
        roots = self._roots(location)
        extensions = self._extensions(file_type)
        needle = name.strip().casefold()
        matches: list[Path] = []
        matches_found = 0
        for _directory, _subdirectories, filenames in self._walk(roots):
            for filename in filenames:
                path = _directory / filename
                if needle and needle not in filename.casefold():
                    continue
                if extensions is not None and path.suffix.casefold() not in extensions:
                    continue
                matches_found += 1
                matches.append(path)
                if not latest and matches_found > MAX_SEARCH_RESULTS:
                    break
            if not latest and matches_found > MAX_SEARCH_RESULTS:
                break

        if latest:
            matches.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        records: list[dict[str, Any]] = []
        for path in matches[:MAX_SEARCH_RESULTS]:
            try:
                records.append(self._file_record(path))
            except OSError as exc:
                self._inaccessible.append(str(exc.filename or path))
        return self._result(records, matches_found, roots)

    def find_folders(self, name: str, location: str | None = None) -> dict[str, Any]:
        roots = self._roots(location)
        needle = name.strip().casefold()
        matches: list[Path] = []
        matches_found = 0
        for directory, child_directories, _files in self._walk(roots):
            for child in child_directories:
                if needle in child.casefold():
                    matches.append(directory / child)
                    matches_found += 1
                    if matches_found > MAX_SEARCH_RESULTS:
                        break
            if matches_found > MAX_SEARCH_RESULTS:
                break
        return self._result(
            [{"path": str(path), "name": path.name} for path in matches[:MAX_SEARCH_RESULTS]],
            matches_found,
            roots,
        )

    def find_recent_files(
        self,
        location: str | None = None,
        days: int = 7,
        file_type: str | None = None,
    ) -> dict[str, Any]:
        roots = self._roots(location)
        extensions = self._extensions(file_type)
        cutoff = datetime.now().timestamp() - timedelta(days=days).total_seconds()
        matches: list[Path] = []
        matches_found = 0
        for directory, _subdirectories, filenames in self._walk(roots):
            for filename in filenames:
                path = directory / filename
                if extensions is not None and path.suffix.casefold() not in extensions:
                    continue
                try:
                    if path.stat().st_mtime >= cutoff:
                        matches.append(path)
                        matches_found += 1
                except OSError:
                    self._inaccessible.append(str(path))
                    continue
        matches.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        records = []
        for path in matches[:MAX_SEARCH_RESULTS]:
            try:
                records.append(self._file_record(path))
            except OSError:
                self._inaccessible.append(str(path))
        return self._result(records, matches_found, roots)


def get_file_info_executor(path: str) -> dict[str, Any]:
    target = Path(path).expanduser().resolve(strict=True)
    if not target.is_file() and not target.is_dir():
        raise FileNotFoundError(f"File or folder '{path}' does not exist.")
    stat = target.stat()
    return {
        "success": True,
        "path": str(target),
        "name": target.name,
        "kind": "folder" if target.is_dir() else "file",
        "size": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
    }


def open_found_file_executor(path: str, application: str | None = None) -> dict[str, Any]:
    target = Path(path).expanduser().resolve(strict=True)
    if not target.is_file():
        raise FileNotFoundError(f"File '{path}' does not exist.")
    if application:
        from flex.tools.app_control import _lookup_application

        resolved = _lookup_application(application)
        if not resolved:
            raise FileNotFoundError(f"Application '{application}' was not found.")
        if resolved.startswith(("ms-", "shell:AppsFolder\\")) or resolved.lower().endswith(".lnk"):
            raise RuntimeError(f"Application '{application}' cannot open a file path.")
        process = subprocess.Popen([resolved, str(target)], shell=False)
        return {"success": True, "action": "opened", "path": str(target), "process_id": process.pid}
    if os.name != "nt":
        raise OSError("Opening files with their default application is only supported on Windows.")
    os.startfile(str(target))
    return {"success": True, "action": "opened", "path": str(target)}


def open_file_location_executor(path: str) -> dict[str, Any]:
    target = Path(path).expanduser().resolve(strict=True)
    folder = target if target.is_dir() else target.parent
    if os.name != "nt":
        raise OSError("Opening file locations in Explorer is only supported on Windows.")
    process = subprocess.Popen(["explorer.exe", "/select," + str(target)] if target.is_file() else ["explorer.exe", str(folder)], shell=False)
    return {"success": True, "action": "opened_location", "path": str(target), "process_id": process.pid}


def _verified_success(_arguments: dict[str, Any], result: dict[str, Any]) -> bool:
    return result.get("success") is True


def _make_search_tool(
    name: str,
    description: str,
    schema: type[BaseModel],
    executor: Any,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        schema=schema.model_json_schema(),
        permission_level=PermissionLevel.READ_ONLY,
        executor=executor,
        argument_model=schema,
        verification=_verified_success,
    )


def create_file_search_tools(search_locations: list[str] | None = None) -> list[ToolSpec]:
    tools = FindFilesTools(search_locations)
    return [
        _make_search_tool(
            "find_files",
            "Find files by name and/or type within common folders (Desktop, Documents, Downloads, Pictures, Videos, OneDrive) and user-selected folders. Set latest=true to sort newest-first. Searches only requested/common folders, never all of C:. Results are capped at 10 and include full paths and modified times.",
            FindFilesArgs,
            tools.find_files,
        ),
        _make_search_tool(
            "find_folders",
            "Find folders by name within common and user-selected folders. Results are capped at 10.",
            FindFoldersArgs,
            tools.find_folders,
        ),
        _make_search_tool(
            "find_recent_files",
            "Find recently modified files, newest first, within common and user-selected folders.",
            FindRecentFilesArgs,
            tools.find_recent_files,
        ),
        _make_search_tool(
            "get_file_info",
            "Get the full path, size, kind, and last-modified time for a file or folder.",
            FilePathArgs,
            get_file_info_executor,
        ),
        _make_search_tool(
            "open_found_file",
            "Open an existing file with its default Windows application or a named application.",
            OpenFoundFileArgs,
            open_found_file_executor,
        ),
        _make_search_tool(
            "open_file_location",
            "Open File Explorer at an existing file or folder; selects a file when one is provided.",
            FilePathArgs,
            open_file_location_executor,
        ),
    ]
