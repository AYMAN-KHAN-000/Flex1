from __future__ import annotations

from pathlib import Path

import pytest

from flex.security.permissions import PermissionLevel
from flex.tools.personal import LocalPersonalStore, create_personal_tools


def test_quick_folders_require_real_folders_and_names_are_case_insensitive(tmp_path):
    store = LocalPersonalStore(tmp_path / "personal.db")
    folder = tmp_path / "My Projects"
    folder.mkdir()

    added = store.add_quick_folder("Projects", str(folder))
    opened_folder = store.get_quick_folder("projects")
    listed = store.get_quick_folders()

    assert added["path"] == str(folder)
    assert opened_folder == folder
    assert listed["folders"][0]["exists"] is True
    with pytest.raises(ValueError, match="already exists"):
        store.add_quick_folder("projects", str(folder))
    with pytest.raises(FileNotFoundError):
        store.add_quick_folder("invalid", str(tmp_path / "missing"))


def test_quick_folder_update_remove_and_stale_path(tmp_path):
    store = LocalPersonalStore(tmp_path / "personal.db")
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    store.add_quick_folder("work", str(first))

    assert store.update_quick_folder("work", str(second))["path"] == str(second)
    assert store.get_quick_folder("work") == second
    assert store.remove_quick_folder("work")["action"] == "removed"
    with pytest.raises(KeyError):
        store.get_quick_folder("work")

    store.add_quick_folder("stale", str(first))
    first.rmdir()
    with pytest.raises(FileNotFoundError, match="no longer exists"):
        store.get_quick_folder("stale")


def test_quick_notes_save_locally_search_today_and_dedupe(tmp_path):
    store = LocalPersonalStore(tmp_path / "personal.db")

    added = store.add_note("Finish FLEX screenshots")
    duplicate = store.add_note("finish flex screenshots")
    searched = store.search_notes("FLEX")
    today = store.search_notes(today_only=True)

    assert added["action"] == "saved"
    assert duplicate["action"] == "duplicate"
    assert searched["count"] == 1
    assert today["notes"][0]["id"] == added["note_id"]


def test_note_delete_requires_unique_match_and_returns_deleted_content(tmp_path):
    store = LocalPersonalStore(tmp_path / "personal.db")
    store.add_note("Buy a mouse")

    deleted = store.delete_note("mouse")

    assert deleted["action"] == "deleted"
    assert deleted["text"] == "Buy a mouse"
    assert store.search_notes("mouse")["count"] == 0


def test_note_delete_rejects_ambiguous_query(tmp_path):
    store = LocalPersonalStore(tmp_path / "personal.db")
    store.add_note("Buy a mouse")
    store.add_note("Clean the mouse pad")

    with pytest.raises(ValueError, match="More than one note matches"):
        store.delete_note("mouse")


def test_personal_tools_mark_delete_high_risk(tmp_path):
    tools = {
        tool.name: tool
        for tool in create_personal_tools(tmp_path / "personal.db")
    }

    assert tools["delete_note"].permission_level == PermissionLevel.HIGH_RISK
    assert tools["list_quick_folders"].permission_level == PermissionLevel.READ_ONLY
    assert tools["search_notes"].permission_level == PermissionLevel.READ_ONLY
