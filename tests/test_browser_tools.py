from __future__ import annotations

import sys
import types
from urllib.parse import parse_qs, urlparse

import pytest

from flex.security.permissions import PermissionLevel
from flex.tools.browser.browser_manager import BrowserManager
from flex.tools.browser.browser_tools import create_browser_tools


class FakeResponse:
    def __init__(self, status=200):
        self.status = status


class FakeLocator:
    def __init__(self, page, count=1, value=""):
        self.page = page
        self.matches = count
        self.value = value

    def count(self):
        return self.matches

    def click(self, timeout):
        if not self.page.no_click_change:
            self.page.clicked = True
            self.page.body_text = "The requested page state changed"

    def fill(self, text, timeout):
        self.value = text

    def input_value(self, timeout):
        return self.value

    def filter(self, has_text):
        return self

    def inner_text(self, timeout):
        return self.page.body_text


class FakePage:
    def __init__(self):
        self.url = "about:blank"
        self.clicked = False
        self.locator_count = 1
        self.response_status = 200
        self.fail_navigation = False
        self.no_click_change = False
        self.body_text = "untrusted page text"

    def is_closed(self):
        return False

    def goto(self, url, **kwargs):
        if self.fail_navigation:
            raise TimeoutError("navigation timeout")
        self.url = url
        query = parse_qs(urlparse(url).query).get("q")
        if query:
            self.body_text = f"Search results for {query[0]}"
        return FakeResponse(self.response_status)

    def title(self):
        return "Test page"

    def set_default_timeout(self, _timeout):
        pass

    def wait_for_load_state(self, *_args, **_kwargs):
        pass

    def get_by_role(self, _role, name):
        return FakeLocator(self, self.locator_count, name)

    def get_by_label(self, value):
        return FakeLocator(self, self.locator_count, value)

    def get_by_placeholder(self, value):
        return FakeLocator(self, self.locator_count, value)

    def get_by_text(self, value):
        return FakeLocator(self, self.locator_count, value)

    def get_by_test_id(self, value):
        return FakeLocator(self, self.locator_count, value)

    def locator(self, value):
        return FakeLocator(self, self.locator_count, value)

    def go_back(self, **_kwargs):
        self.url = "https://example.org/previous"
        return FakeResponse()

    def reload(self, **_kwargs):
        return FakeResponse()


class FakeContext:
    def __init__(self):
        self.pages = [FakePage()]

    def set_default_timeout(self, _timeout):
        pass

    def new_page(self):
        page = FakePage()
        self.pages.append(page)
        return page

    def close(self):
        self.pages.clear()


class FakeChromium:
    def __init__(self):
        self.context = FakeContext()
        self.fail_launch = False

    def launch_persistent_context(self, *_args, **_kwargs):
        if self.fail_launch:
            raise RuntimeError("browser binary missing")
        return self.context


class FakePlaywright:
    def __init__(self):
        self.chromium = FakeChromium()

    def stop(self):
        pass


@pytest.fixture
def fake_playwright(monkeypatch):
    playwright = FakePlaywright()
    package = types.ModuleType("playwright")
    package.__path__ = []
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: types.SimpleNamespace(start=lambda: playwright)
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    return playwright


def test_browser_open_navigate_and_search_verify_current_page(tmp_path, fake_playwright):
    manager = BrowserManager(profile_root=tmp_path)
    try:
        opened = manager.open("chromium", "https://example.org")
        sensitive_url = manager.navigate(
            "https://example.org/?access_token=hidden-token&topic=playwright"
        )
        navigated = manager.navigate("https://example.org/docs")
        searched = manager.search("browser automation", "duckduckgo")

        assert opened["success"] is True
        assert "hidden-token" not in sensitive_url["url"]
        assert "topic=playwright" in sensitive_url["url"]
        assert navigated["url"] == "https://example.org/docs"
        assert searched["success"] is True
        assert parse_qs(urlparse(searched["url"]).query)["q"] == ["browser automation"]
    finally:
        manager.close()


def test_browser_locator_success_and_ambiguity(tmp_path, fake_playwright):
    manager = BrowserManager(profile_root=tmp_path)
    try:
        manager.open("chromium", "https://example.org")
        clicked = manager.click("role", "Continue", role="button")
        assert clicked["success"] is True

        fake_playwright.chromium.context.pages[-1].locator_count = 2
        ambiguous = manager.click("label", "Continue")
        assert ambiguous["error"]["code"] == "AMBIGUOUS_TARGET"
    finally:
        manager.close()


def test_browser_type_is_verified_and_consequential_actions_are_gated(tmp_path, fake_playwright):
    manager = BrowserManager(profile_root=tmp_path)
    try:
        manager.open("chromium", "https://example.org")
        result = manager.type_text("label", "Search", "FLEX")
        assert result["success"] is True
        tools = {tool.name: tool for tool in create_browser_tools(manager)}
        assert tools["browser_open"].permission_level == PermissionLevel.LOW_RISK
        assert tools["browser_click"].permission_level == PermissionLevel.HIGH_RISK
        assert tools["browser_type"].permission_level == PermissionLevel.HIGH_RISK
    finally:
        manager.close()
def test_browser_timeout_and_navigation_verification_failure(tmp_path, fake_playwright):
    manager = BrowserManager(profile_root=tmp_path)
    try:
        fake_playwright.chromium.context.pages[0].fail_navigation = True
        timeout = manager.open("chromium", "https://example.org")
        assert timeout["error"]["code"] == "BROWSER_TIMEOUT"
    finally:
        manager.close()

    fake_playwright.chromium.context = FakeContext()
    manager = BrowserManager(profile_root=tmp_path)
    try:
        fake_playwright.chromium.context.pages[0].response_status = 503
        failed = manager.open("chromium", "https://example.org")
        assert failed["error"]["code"] == "BROWSER_NAVIGATION_FAILED"
    finally:
        manager.close()


def test_browser_click_without_observable_change_fails_verification(tmp_path, fake_playwright):
    manager = BrowserManager(profile_root=tmp_path)
    try:
        manager.open("chromium", "https://example.org")
        fake_playwright.chromium.context.pages[-1].no_click_change = True

        result = manager.click("text", "Continue")

        assert result["success"] is False
        assert result["error"]["code"] == "BROWSER_VERIFICATION_FAILED"
    finally:
        manager.close()


def test_browser_launch_failure_is_structured(tmp_path, fake_playwright):
    fake_playwright.chromium.fail_launch = True
    manager = BrowserManager(profile_root=tmp_path)
    try:
        result = manager.open("chromium", "https://example.org")
        assert result["success"] is False
        assert result["error"]["code"] == "BROWSER_NOT_AVAILABLE"
    finally:
        manager.close()
