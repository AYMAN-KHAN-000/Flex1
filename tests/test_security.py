from __future__ import annotations

from pathlib import Path

import pytest

from flex.security.permissions import (
    PermissionLevel,
    PermissionManager,
    PermissionRequest,
)
from flex.security.validator import ArgumentValidator, ToolValidationError
from flex.tools.filesystem import _safe_directory, create_folder_executor, create_folder_verifier


def test_permission_manager_allows_low_risk_without_confirmation():
    manager = PermissionManager(require_confirmation=False)
    decision = manager.can_execute("open_application", PermissionLevel.LOW_RISK)
    assert decision.allowed is True


def test_permission_manager_requests_one_confirmation_for_a_protected_batch():
    prompts = []
    requests = [
        PermissionRequest(
            tool_name="close_application",
            required_permission=PermissionLevel.HIGH_RISK,
            details='{"application": "Notepad"}',
            description="Close an application",
        ),
        PermissionRequest(
            tool_name="close_application",
            required_permission=PermissionLevel.HIGH_RISK,
            details='{"application": "Chrome"}',
            description="Close an application",
        ),
    ]
    manager = PermissionManager(
        confirmation_callback=lambda name, details: (
            prompts.append((name, details)) or False
        )
    )

    decisions = manager.can_execute_batch(requests)

    assert len(prompts) == 1
    assert prompts[0][0] == "FLEX workflow"
    assert all(not decision.allowed for decision in decisions.values())


def test_argument_validator_rejects_path_traversal():
    try:
        ArgumentValidator.ensure_safe_path("../secret.txt")
    except ToolValidationError:
        return
    raise AssertionError("expected ToolValidationError")


def test_desktop_alias_resolves_under_current_users_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)

    expected = (tmp_path / "Desktop" / "FLEX_TEST").resolve()
    assert _safe_directory("Desktop/FLEX_TEST") == expected


def test_create_folder_result_verification_uses_arguments_and_result(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)

    result = create_folder_executor("Desktop/FLEX_TEST")
    assert create_folder_verifier({"path": "Desktop/FLEX_TEST"}, result) is True


def test_filesystem_access_rejects_outside_user_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "profile")
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path / "profile")

    with pytest.raises(ToolValidationError, match="current user's profile"):
        _safe_directory(str(tmp_path / "outside"))
