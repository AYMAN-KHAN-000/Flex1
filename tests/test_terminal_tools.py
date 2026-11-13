from __future__ import annotations

import sys
import subprocess
from pathlib import Path

from flex.security.permissions import PermissionLevel
from flex.tools.terminal import SafeTerminal, create_terminal_tools


def test_allowed_python_version_and_git_status(tmp_path):
    terminal = SafeTerminal(tmp_path, max_output=2_000)
    version = terminal.python_version()
    assert version["success"] is True
    assert sys.version.split()[0] in version["stdout"] or sys.version.split()[0] in version["stderr"]

    git = subprocess.run(
        ["git", "init", "--quiet", str(tmp_path)],
        capture_output=True,
        check=False,
        shell=False,
    )
    if git.returncode != 0:
        return
    status = terminal.git_status(str(tmp_path))
    assert status["success"] is True
    assert status["stdout"].startswith("## ")


def test_blocked_paths_and_working_directory_validation(tmp_path):
    terminal = SafeTerminal(tmp_path / "project")
    (tmp_path / "project").mkdir()
    outside = Path("C:/Windows")

    blocked = terminal.pwd(str(outside))
    missing = terminal.pwd(str(tmp_path / "missing"))
    assert blocked["error"]["code"] == "PERMISSION_DENIED"
    assert missing["error"]["code"] == "INVALID_WORKING_DIRECTORY"


def test_python_script_nonzero_exit_and_output_truncation(tmp_path):
    terminal = SafeTerminal(tmp_path, max_output=100)
    failing = tmp_path / "failure.py"
    failing.write_text("raise SystemExit(7)\n", encoding="utf-8")
    failed = terminal.run_python(str(failing))
    assert failed["success"] is False
    assert failed["exit_code"] == 7
    assert failed["error"]["code"] == "TERMINAL_EXIT_FAILURE"

    large = tmp_path / "large.py"
    large.write_text("print('x' * 10000)\n", encoding="utf-8")
    result = terminal.run_python(str(large))
    assert result["success"] is True
    assert result["truncated"] is True
    assert len(result["stdout"].encode()) + len(result["stderr"].encode()) <= 100


def test_python_timeout_kills_process_and_returns_structured_error(tmp_path):
    terminal = SafeTerminal(tmp_path, timeout_ms=1_000)
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")

    result = terminal.run_python(str(script))

    assert result["success"] is False
    assert result["timed_out"] is True
    assert result["error"]["code"] == "TERMINAL_TIMEOUT"


def test_terminal_output_redacts_credentials(tmp_path):
    terminal = SafeTerminal(tmp_path, max_output=500)
    script = tmp_path / "credential.py"
    script.write_text(
        "print('password=private-value')\n"
        "print('Authorization: Bearer hidden-token')\n",
        encoding="utf-8",
    )

    result = terminal.run_python(str(script))

    assert result["success"] is True
    assert "private-value" not in result["stdout"]
    assert "hidden-token" not in result["stdout"]


def test_terminal_tools_are_read_only_except_confirmed_script_execution(tmp_path):
    tools = {tool.name: tool for tool in create_terminal_tools(SafeTerminal(tmp_path))}
    assert tools["terminal_git_status"].permission_level == PermissionLevel.READ_ONLY
    assert tools["terminal_run_python"].permission_level == PermissionLevel.HIGH_RISK
    assert "terminal_run_shell" not in tools
