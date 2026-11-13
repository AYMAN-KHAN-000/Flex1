from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from flex.security.permissions import PermissionLevel
from flex.security.redaction import redact_sensitive_text
from flex.tools.registry import ToolSpec


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DirectoryArgs(StrictArgs):
    working_directory: str | None = Field(
        default=None,
        description="Directory under the FLEX project or current user's profile.",
    )


class FileInfoArgs(DirectoryArgs):
    path: str = Field(min_length=1, max_length=1000)


class PythonScriptArgs(DirectoryArgs):
    script: str = Field(min_length=1, max_length=1000)
    arguments: list[str] = Field(default_factory=list, max_length=20)
    timeout_ms: int | None = Field(default=None, ge=1000, le=300000)


class GitDiffArgs(DirectoryArgs):
    staged: bool = False


def _error(code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {
        "success": False,
        "error": {"code": code, "message": message, "retryable": retryable},
    }


class SafeTerminal:
    """Execute a small allowlist of direct, non-shell developer commands."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        timeout_ms: int = 30_000,
        max_output: int = 20_000,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.allowed_roots = (self.project_root, Path.home().resolve())
        self.timeout_ms = timeout_ms
        self.max_output = max_output
        self.cancel_event = cancel_event

    def _working_directory(self, supplied: str | None) -> Path:
        candidate = Path(supplied).expanduser() if supplied else self.project_root
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"Working directory does not exist: {candidate}") from exc
        if not resolved.is_dir():
            raise ValueError(f"Working directory is not a directory: {resolved}")
        if not any(
            resolved == root or root in resolved.parents for root in self.allowed_roots
        ):
            raise PermissionError(
                "Working directory must be inside the FLEX project or current user's profile."
            )
        return resolved

    def _resolve_file(self, path: str, working_directory: Path) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = working_directory / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise FileNotFoundError(f"Path does not exist: {candidate}") from exc
        if not any(
            resolved == root or root in resolved.parents for root in self.allowed_roots
        ):
            raise PermissionError("Path must be inside an allowed FLEX working directory.")
        return resolved

    def _run(
        self,
        arguments: list[str],
        working_directory: Path,
        *,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        limit_ms = timeout_ms or self.timeout_ms
        started = time.monotonic()
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        captured = 0
        truncated = False
        lock = threading.Lock()
        process: subprocess.Popen[bytes] | None = None
        timed_out = False
        cancelled = False

        try:
            process = subprocess.Popen(
                arguments,
                cwd=working_directory,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except FileNotFoundError:
            return _error("TERMINAL_NOT_AVAILABLE", f"Command is unavailable: {arguments[0]}")
        except OSError as exc:
            return _error("TERMINAL_EXECUTION_FAILED", str(exc))

        def drain(stream: Any, name: str) -> None:
            nonlocal captured, truncated
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    return
                with lock:
                    remaining = self.max_output - captured
                    if remaining > 0:
                        accepted = chunk[:remaining]
                        buffers[name].extend(accepted)
                        captured += len(accepted)
                    if len(chunk) > max(remaining, 0):
                        truncated = True

        readers = (
            threading.Thread(target=drain, args=(process.stdout, "stdout"), daemon=True),
            threading.Thread(target=drain, args=(process.stderr, "stderr"), daemon=True),
        )
        for reader in readers:
            reader.start()

        deadline = started + limit_ms / 1000
        while process.poll() is None:
            if self.cancel_event is not None and self.cancel_event.is_set():
                cancelled = True
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            try:
                process.wait(timeout=min(0.1, max(deadline - time.monotonic(), 0.01)))
            except subprocess.TimeoutExpired:
                continue

        if timed_out or cancelled:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for reader in readers:
            reader.join(timeout=2)

        duration_ms = round((time.monotonic() - started) * 1000)
        stdout = redact_sensitive_text(
            bytes(buffers["stdout"]).decode("utf-8", errors="replace")
        )
        stderr = redact_sensitive_text(
            bytes(buffers["stderr"]).decode("utf-8", errors="replace")
        )
        result = {
            "success": not timed_out and not cancelled and process.returncode == 0,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": process.returncode,
            "truncated": truncated,
            "duration_ms": duration_ms,
            "timed_out": timed_out,
        }
        if timed_out:
            result["error"] = {
                "code": "TERMINAL_TIMEOUT",
                "message": f"Command exceeded its {limit_ms} ms time limit.",
                "retryable": True,
            }
        elif cancelled:
            result["error"] = {
                "code": "WORKFLOW_CANCELLED",
                "message": "Command was stopped at the user's request.",
                "retryable": False,
            }
        elif process.returncode != 0:
            result["error"] = {
                "code": "TERMINAL_EXIT_FAILURE",
                "message": f"Command exited with status {process.returncode}.",
                "retryable": False,
            }
        return result

    def pwd(self, working_directory: str | None = None) -> dict[str, Any]:
        try:
            directory = self._working_directory(working_directory)
        except PermissionError as exc:
            return _error("PERMISSION_DENIED", str(exc))
        except ValueError as exc:
            return _error("INVALID_WORKING_DIRECTORY", str(exc))
        return {"success": True, "path": str(directory)}

    def list_directory(self, working_directory: str | None = None) -> dict[str, Any]:
        try:
            directory = self._working_directory(working_directory)
            entries = sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold()))
        except PermissionError as exc:
            return _error("PERMISSION_DENIED", str(exc))
        except (OSError, ValueError) as exc:
            return _error("INVALID_WORKING_DIRECTORY", str(exc))
        return {
            "success": True,
            "path": str(directory),
            "entries": [
                {"name": entry.name, "type": "directory" if entry.is_dir() else "file"}
                for entry in entries[:200]
            ],
            "truncated": len(entries) > 200,
        }

    def file_info(self, path: str, working_directory: str | None = None) -> dict[str, Any]:
        try:
            directory = self._working_directory(working_directory)
            target = self._resolve_file(path, directory)
            info = target.stat()
        except PermissionError as exc:
            return _error("PERMISSION_DENIED", str(exc))
        except FileNotFoundError as exc:
            return _error("TERMINAL_TARGET_NOT_FOUND", str(exc))
        except (OSError, ValueError) as exc:
            return _error("INVALID_WORKING_DIRECTORY", str(exc))
        return {
            "success": True,
            "path": str(target),
            "type": "directory" if target.is_dir() else "file",
            "size_bytes": info.st_size,
            "modified_time": info.st_mtime,
        }

    def python_version(self, working_directory: str | None = None) -> dict[str, Any]:
        try:
            directory = self._working_directory(working_directory)
        except PermissionError as exc:
            return _error("PERMISSION_DENIED", str(exc))
        except ValueError as exc:
            return _error("INVALID_WORKING_DIRECTORY", str(exc))
        return self._run([sys.executable, "--version"], directory)

    def git_status(self, working_directory: str | None = None) -> dict[str, Any]:
        return self._git(["status", "--short", "--branch"], working_directory)

    def git_diff(
        self,
        working_directory: str | None = None,
        staged: bool = False,
    ) -> dict[str, Any]:
        arguments = ["diff", "--stat"]
        if staged:
            arguments.insert(1, "--cached")
        return self._git(arguments, working_directory)

    def _git(self, git_arguments: list[str], working_directory: str | None) -> dict[str, Any]:
        try:
            directory = self._working_directory(working_directory)
        except PermissionError as exc:
            return _error("PERMISSION_DENIED", str(exc))
        except ValueError as exc:
            return _error("INVALID_WORKING_DIRECTORY", str(exc))
        git = shutil.which("git")
        if git is None:
            return _error("TERMINAL_NOT_AVAILABLE", "Git was not found on PATH.")
        return self._run([git, *git_arguments], directory)

    def run_python(
        self,
        script: str,
        arguments: list[str] | None = None,
        working_directory: str | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        try:
            directory = self._working_directory(working_directory)
            script_path = self._resolve_file(script, directory)
        except PermissionError as exc:
            return _error("PERMISSION_DENIED", str(exc))
        except FileNotFoundError as exc:
            return _error("TERMINAL_TARGET_NOT_FOUND", str(exc))
        except (OSError, ValueError) as exc:
            return _error("INVALID_WORKING_DIRECTORY", str(exc))
        if not script_path.is_file() or script_path.suffix.lower() != ".py":
            return _error("TERMINAL_BLOCKED", "Only an existing Python script can be run.")
        return self._run(
            [sys.executable, str(script_path), *(arguments or [])],
            directory,
            timeout_ms=timeout_ms,
        )

    def system_info(self) -> dict[str, Any]:
        return {
            "success": True,
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
        }


def _make_tool(
    name: str,
    description: str,
    args_model: type[BaseModel],
    executor: Any,
    permission: PermissionLevel,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        schema=args_model.model_json_schema(),
        permission_level=permission,
        executor=executor,
        argument_model=args_model,
        verification=lambda _arguments, result: result.get("success") is True,
    )


def create_terminal_tools(terminal: SafeTerminal) -> list[ToolSpec]:
    return [
        _make_tool(
            "terminal_pwd",
            "Return the validated current working directory, defaulting to the FLEX project.",
            DirectoryArgs,
            terminal.pwd,
            PermissionLevel.READ_ONLY,
        ),
        _make_tool(
            "terminal_list_directory",
            "List names and types in a directory under the FLEX project or user profile.",
            DirectoryArgs,
            terminal.list_directory,
            PermissionLevel.READ_ONLY,
        ),
        _make_tool(
            "terminal_file_info",
            "Read metadata for one existing file or directory within an allowed working root.",
            FileInfoArgs,
            terminal.file_info,
            PermissionLevel.READ_ONLY,
        ),
        _make_tool(
            "terminal_python_version",
            "Check the configured Python interpreter version without running user code.",
            DirectoryArgs,
            terminal.python_version,
            PermissionLevel.READ_ONLY,
        ),
        _make_tool(
            "terminal_git_status",
            "Read the Git working tree status in an explicitly validated project directory.",
            DirectoryArgs,
            terminal.git_status,
            PermissionLevel.READ_ONLY,
        ),
        _make_tool(
            "terminal_git_diff",
            "Return a bounded summary of unstaged or staged Git changes.",
            GitDiffArgs,
            terminal.git_diff,
            PermissionLevel.READ_ONLY,
        ),
        _make_tool(
            "terminal_system_info",
            "Return basic operating system and Python version information.",
            StrictArgs,
            terminal.system_info,
            PermissionLevel.READ_ONLY,
        ),
        _make_tool(
            "terminal_run_python",
            "Run one existing Python script under an allowed directory. Requires explicit user confirmation.",
            PythonScriptArgs,
            terminal.run_python,
            PermissionLevel.HIGH_RISK,
        ),
    ]
