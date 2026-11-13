from __future__ import annotations

import pytest

from flex.memory.database import MemoryDatabase
from flex.memory.tools import create_memory_tools
from flex.memory.usage_memory import UsageMemory, classify_path
from flex.security.permissions import PermissionLevel


def test_structured_memory_save_duplicate_search_get_and_update(tmp_path):
    database = MemoryDatabase(tmp_path / "flex.db")
    saved = database.save_memory("preference", "preferred_editor", "VS Code")
    memory_id = saved["memory"]["id"]

    duplicate = database.save_memory("preference", "PREFERRED_EDITOR", "vs code")
    assert saved["action"] == "saved"
    assert duplicate["action"] == "duplicate"
    assert database.search_memories("code")[0]["key"] == "preferred_editor"
    assert database.get_memory(category="preference", key="PREFERRED_EDITOR")["id"] == memory_id

    updated = database.update_memory(
        memory_id,
        category="preference",
        key="preferred_editor",
        value="VSCodium",
    )
    assert updated["value"] == "VSCodium"
    assert updated["updated_at"] >= updated["created_at"]


def test_memory_delete_and_invalid_or_secret_values(tmp_path):
    database = MemoryDatabase(tmp_path / "flex.db")
    memory_id = database.save_memory("project", "current_project", "FLEX")["memory"]["id"]

    database.delete_memory(memory_id)
    with pytest.raises(KeyError):
        database.get_memory(memory_id=memory_id)
    with pytest.raises(ValueError, match="Unsupported memory category"):
        database.save_memory("conversation", "question", "temporary")
    with pytest.raises(ValueError, match="does not store"):
        database.save_memory("fact", "api_key", "example")
    with pytest.raises(ValueError, match="does not store"):
        database.save_memory("fact", "credential", "Bearer abc123")


def test_memory_tools_are_confirmation_gated_and_search_is_read_only(tmp_path):
    tools = {tool.name: tool for tool in create_memory_tools(str(tmp_path / "flex.db"))}

    assert tools["memory_save"].permission_level == PermissionLevel.HIGH_RISK
    assert tools["memory_update"].permission_level == PermissionLevel.HIGH_RISK
    assert tools["memory_delete"].permission_level == PermissionLevel.HIGH_RISK
    assert tools["memory_search"].permission_level == PermissionLevel.READ_ONLY


def test_memory_list_uses_its_validated_arguments(tmp_path):
    tools = {tool.name: tool for tool in create_memory_tools(str(tmp_path / "flex.db"))}
    tools["memory_save"].executor(
        category="preference",
        key="preferred_editor",
        value="VS Code",
    )
    listed = tools["memory_list"].executor(category="preference", limit=5)

    assert listed["success"] is True
    assert listed["memories"][0]["value"] == "VS Code"


def test_usage_memory_deduplicates_paths_and_ranks_recent_and_frequent(tmp_path):
    database = UsageMemory(tmp_path / "flex.db")
    document = tmp_path / "Biology Notes.pdf"

    first = database.record_open(
        item_type=classify_path(document),
        name=document.name,
        location=str(document),
        category="study",
        tags=["biology", "notes"],
    )
    second = database.record_open(
        item_type="pdf",
        name=document.name,
        location=str(document),
        category="study",
        tags=["biology", "notes"],
    )

    assert first["id"] == second["id"]
    assert second["open_count"] == 2
    assert second["last_opened"] >= second["first_seen"]
    assert database.search("biology")[0]["name"] == "Biology Notes.pdf"
    assert database.list_items(item_type="pdf", sort="frequent")[0]["open_count"] == 2


def test_usage_memory_sanitizes_website_data_and_supports_record_management(tmp_path):
    database = UsageMemory(tmp_path / "flex.db")
    website = database.record_open(
        item_type="website",
        name="example.com",
        location="https://example.com/private/path?token=not-to-store#fragment",
    )
    document = database.record_open(
        item_type="pdf",
        name="Notes.pdf",
        location=str(tmp_path / "Notes.pdf"),
    )

    assert website["location"] == "https://example.com"
    assert "token" not in website["location"]
    database.delete_item(document["id"])
    with pytest.raises(KeyError):
        database.delete_item(document["id"])
    assert database.clear() == 1
    assert database.list_items() == []
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        database.record_open(
            item_type="website",
            name="invalid",
            location="file:///private.txt",
        )


def test_study_folder_index_only_records_supported_document_metadata(tmp_path):
    database = UsageMemory(tmp_path / "flex.db")
    study_folder = tmp_path / "School"
    study_folder.mkdir()
    (study_folder / "Physics Book.pdf").write_bytes(b"not read by indexer")
    (study_folder / "notes.docx").write_bytes(b"not read by indexer")
    (study_folder / "video.mp4").write_bytes(b"not a study document")

    assert database.index_study_folder(study_folder) == 2
    indexed = database.list_items(sort="frequent")
    assert {item["name"] for item in indexed} == {"Physics Book.pdf", "notes.docx"}
    assert all(item["open_count"] == 0 for item in indexed)
    assert all(item["category"] == "study" for item in indexed)
