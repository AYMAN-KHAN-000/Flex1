import os
from pathlib import Path
from types import SimpleNamespace

from flex.tools.browser import browser_manager


def test_frozen_app_uses_bundled_playwright_browser(tmp_path, monkeypatch) -> None:
    browser_root = tmp_path / "playwright-browsers"
    browser_root.mkdir()
    monkeypatch.setattr(
        browser_manager,
        "sys",
        SimpleNamespace(frozen=True, _MEIPASS=str(tmp_path), executable="FLEX.exe"),
    )
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)

    browser_manager.BrowserManager._configure_bundled_browser_path()

    assert Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]) == browser_root
