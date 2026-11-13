from __future__ import annotations

from pathlib import Path

import pytest

from flex.tools.file_search import FindFilesTools
from flex.tools.filesystem import search_files_executor


def test_find_files_searches_only_known_roots_and_returns_full_paths(tmp_path, monkeypatch):
    home = tmp_path / "user"
    desktop = home / "Desktop"
    downloads = home / "Downloads"
    desktop.mkdir(parents=True)
    downloads.mkdir()
    (desktop / "main.py").write_text("print('hi')", encoding="utf-8")
    (downloads / "main.png").write_bytes(b"image")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(
        "flex.tools.file_search._known_user_folder",
        lambda name: home / name,
    )

    result = FindFilesTools().find_files(name="main", file_type="images")

    assert result["count"] == 1
    assert result["results"][0]["path"] == str(downloads / "main.png")
    assert str(desktop) in result["locations_searched"]
    assert result["inaccessible_count"] == 3


def test_find_files_rejects_unapproved_locations(tmp_path):
    with pytest.raises(ValueError, match="common folder or a folder inside"):
        FindFilesTools().find_files(location=str(tmp_path))


def test_find_files_allows_nested_user_selected_folder(tmp_path):
    selected = tmp_path / "Projects"
    nested = selected / "FLEX"
    nested.mkdir(parents=True)
    target = nested / "flex.py"
    target.write_text("", encoding="utf-8")
    tools = FindFilesTools([str(selected)])

    result = tools.find_files(name="flex.py", location=str(nested))

    assert result["count"] == 1
    assert result["results"][0]["path"] == str(target)


def test_find_folders_and_recent_files(tmp_path, monkeypatch):
    home = tmp_path / "user"
    desktop = home / "Desktop"
    projects = desktop / "Projects"
    projects.mkdir(parents=True)
    latest_pdf = projects / "latest.pdf"
    latest_pdf.write_text("pdf", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(
        "flex.tools.file_search._known_user_folder",
        lambda name: home / name,
    )

    tools = FindFilesTools()
    folders = tools.find_folders("project", location="Desktop")
    recent = tools.find_recent_files(location="Desktop", days=1, file_type="pdf")

    assert folders["results"] == [{"path": str(projects), "name": "Projects"}]
    assert recent["results"][0]["path"] == str(latest_pdf)


def test_find_files_caps_results_and_reports_truncation(tmp_path):
    for index in range(12):
        (tmp_path / f"file-{index}.txt").write_text("", encoding="utf-8")
    tools = FindFilesTools([str(tmp_path)])

    result = tools.find_files(location=str(tmp_path))

    assert result["count"] == 10
    assert result["truncated"] is True


def test_legacy_search_files_tool_is_also_bounded(tmp_path, monkeypatch):
    for index in range(12):
        (tmp_path / f"file-{index}.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr("flex.tools.filesystem._safe_directory", lambda _path: tmp_path)

    result = search_files_executor(str(tmp_path), "*.txt")

    assert len(result["matches"]) == 10
    assert result["truncated"] is True
