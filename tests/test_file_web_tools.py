from __future__ import annotations

from types import SimpleNamespace

import pytest

from flex.tools import app_control, desktop, filesystem


def test_open_website_uses_named_browser_for_site_name(monkeypatch):
    calls = []
    monkeypatch.setattr(desktop, "_lookup_application", lambda browser: f"C:\\Apps\\{browser}.exe")
    monkeypatch.setattr(
        desktop.subprocess,
        "Popen",
        lambda args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(pid=123),
    )

    result = desktop.open_website_executor("YouTube", "chrome")

    assert calls == [
        (
            ["C:\\Apps\\chrome.exe", "https://YouTube.com"],
            {"shell": False},
        )
    ]
    assert result["status"] == "opened"
    assert desktop._open_website_verifier({}, result) is True


def test_open_website_preserves_direct_url_in_default_browser(monkeypatch):
    calls = []
    monkeypatch.setattr(
        desktop.webbrowser,
        "open",
        lambda url, new: calls.append((url, new)) or True,
    )

    result = desktop.open_website_executor("https://microsoft.com/products")

    assert calls == [("https://microsoft.com/products", 2)]
    assert result["url"] == "https://microsoft.com/products"


@pytest.mark.parametrize("website", ["javascript:alert(1)", "https://user:pass@example.com"])
def test_open_website_rejects_unsafe_urls(website):
    with pytest.raises(ValueError):
        desktop._website_url(website)


def test_vscode_names_resolve_to_code_executable(monkeypatch):
    monkeypatch.setattr(
        app_control.shutil,
        "which",
        lambda executable: executable if executable == "code.exe" else None,
    )

    assert app_control._lookup_application("VS Code") == "code.exe"
    assert app_control._lookup_application("Visual Studio Code") == "code.exe"


def test_create_file_writes_exact_text_and_does_not_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(filesystem.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(
        filesystem,
        "_known_user_folder",
        lambda name: tmp_path / name,
    )
    path = "Documents/FLEX.txt"
    content = "hello\r\nfrom FLEX"

    result = filesystem.create_file_executor(path, content)

    assert result["status"] == "created"
    assert (tmp_path / "Documents" / "FLEX.txt").read_bytes() == content.encode("utf-8")
    assert filesystem.create_file_verifier({"path": path, "content": content}, result) is True
    with pytest.raises(FileExistsError):
        filesystem.create_file_executor(path, "replace")


def test_create_file_rejects_paths_outside_user_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(filesystem.Path, "home", lambda: tmp_path)

    with pytest.raises(ValueError, match="limited to the current user's profile"):
        filesystem.create_file_executor(str(tmp_path.parent / "outside.txt"), "text")


def test_open_file_launches_requested_editor_with_file_path(tmp_path, monkeypatch):
    target = tmp_path / "FLEX.txt"
    target.write_text("hello", encoding="utf-8")
    calls = []
    monkeypatch.setattr(filesystem, "_safe_directory", lambda _path: target)
    monkeypatch.setattr(filesystem.subprocess, "Popen", lambda args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(pid=456))
    monkeypatch.setattr(app_control, "_lookup_application", lambda application: f"C:\\Apps\\{application}.exe")

    result = filesystem.open_file_executor(str(target), "VS Code")

    assert calls == [([r"C:\Apps\VS Code.exe", str(target)], {"shell": False})]
    assert result["status"] == "opened"
    assert filesystem.open_file_verifier({}, result) is True


@pytest.mark.parametrize(
    ("platform", "account", "expected_url"),
    [
        ("YouTube", "@MrBeast", "https://www.youtube.com/@MrBeast"),
        (
            "YouTube",
            "MrBeast",
            "https://www.youtube.com/results?search_query=MrBeast",
        ),
        (
            "YouTube",
            "Mr Beast",
            "https://www.youtube.com/results?search_query=Mr+Beast",
        ),
        (
            "Instagram",
            "@example_creator",
            "https://www.instagram.com/example_creator/",
        ),
        (
            "TikTok",
            "@example_creator",
            "https://www.tiktok.com/@example_creator",
        ),
    ],
)
def test_open_social_account_opens_handle_or_search(monkeypatch, platform, account, expected_url):
    calls = []
    monkeypatch.setattr(
        desktop.webbrowser,
        "open",
        lambda url, new: calls.append((url, new)) or True,
    )

    result = desktop.open_social_account_executor(platform, account)

    assert calls == [(expected_url, 2)]
    assert result["url"] == expected_url
    assert desktop._open_social_account_verifier({}, result) is True


def test_retired_youtube_custom_url_falls_back_to_channel_search():
    assert desktop._website_url("https://youtube.com/c/MrBeast") == (
        "https://www.youtube.com/results?search_query=MrBeast"
    )


def test_open_social_account_honors_named_browser(monkeypatch):
    calls = []
    monkeypatch.setattr(desktop, "_lookup_application", lambda _browser: "chrome.exe")
    monkeypatch.setattr(
        desktop.subprocess,
        "Popen",
        lambda args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(pid=234),
    )

    result = desktop.open_social_account_executor("YouTube", "@MrBeast", "chrome")

    assert calls == [
        (["chrome.exe", "https://www.youtube.com/@MrBeast"], {"shell": False})
    ]
    assert result["process_id"] == 234
