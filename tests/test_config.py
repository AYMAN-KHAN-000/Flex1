from __future__ import annotations

from pathlib import Path

from flex.config import Settings


def test_settings_load_reads_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "demo-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "openrouter/auto")
    monkeypatch.setenv("REQUIRE_CONFIRMATION", "false")
    settings = Settings.load(Path("."))
    assert settings.openrouter_api_key == "demo-key"
    assert settings.openrouter_model == "openrouter/auto"
    assert settings.require_confirmation is False


def test_settings_default_theme_is_dark():
    settings = Settings(project_root=Path("."))
    assert settings.theme == "dark"


def test_settings_load_reads_edge_indicator_configuration(monkeypatch):
    monkeypatch.setenv("FLEX_EDGE_LINE_COLOR", "#999999")
    monkeypatch.setenv("FLEX_EDGE_LINE_LENGTH", "180")
    monkeypatch.setenv("FLEX_EDGE_LINE_THICKNESS", "7")

    settings = Settings.load(Path("."))

    assert settings.edge_line_color == "#999999"
    assert settings.edge_line_length == 180
    assert settings.edge_line_thickness == 7


def test_settings_load_reads_windows_utility_configuration(monkeypatch):
    monkeypatch.setenv("FLEX_CLIPBOARD_ACCESS", "true")
    monkeypatch.setenv("FLEX_SCREENSHOT_ALL_MONITORS", "true")
    monkeypatch.setenv("FLEX_BRIGHTNESS_HIGH", "82")
    monkeypatch.setenv("FLEX_BRIGHTNESS_MAXIMUM", "150")

    settings = Settings.load(Path("."))

    assert settings.clipboard_access_enabled is True
    assert settings.screenshot_all_monitors is True
    assert settings.brightness_high == 82
    assert settings.brightness_maximum == 100


def test_settings_loads_bounded_automation_and_subsystem_options(monkeypatch):
    monkeypatch.setenv("FLEX_BROWSER_ENABLED", "false")
    monkeypatch.setenv("FLEX_BROWSER_DEFAULT", "edge")
    monkeypatch.setenv("FLEX_BROWSER_TIMEOUT_MS", "500")
    monkeypatch.setenv("FLEX_BROWSER_MAX_TABS", "40")
    monkeypatch.setenv("FLEX_TERMINAL_MAX_OUTPUT", "40000")
    monkeypatch.setenv("FLEX_MEMORY_CONFIRMATION", "false")
    monkeypatch.setenv("FLEX_AUTOMATION_MAX_STEPS", "200")
    monkeypatch.setenv("FLEX_AUTOMATION_MAX_RETRIES", "-1")

    settings = Settings.load(Path("."))

    assert settings.browser_enabled is False
    assert settings.browser_default == "edge"
    assert settings.browser_timeout_ms == 1_000
    assert settings.browser_max_tabs == 20
    assert settings.terminal_max_output == 40_000
    assert settings.memory_confirmation is False
    assert settings.automation_max_steps == 100
    assert settings.automation_max_retries == 0
