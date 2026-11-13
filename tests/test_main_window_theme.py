from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox, QPushButton

from flex.ai.schemas import AIResponse
from flex.config import Settings
from flex.discord.manager import DiscordManager, verify_password
from flex.security.permissions import PermissionLevel
from flex.tools.app_control import OPEN_APPLICATION_TOOL
from flex.tools.registry import ToolSpec
from flex.ui.edge_overlay import FlexEdgeOverlay
from flex.ui.main_window import FlexMainWindow
from flex.voice import SelectionMonitorWorker


@pytest.fixture(autouse=True)
def avoid_starting_windows_selection_process(monkeypatch):
    monkeypatch.setattr(SelectionMonitorWorker, "start", lambda _self: None)


def test_reference_theme_navigation_shows_composer_only_in_chat(
    monkeypatch,
    tmp_path,
):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        FlexEdgeOverlay,
        "_install_global_mouse_hook",
        lambda _self: None,
    )
    window = FlexMainWindow(
        Settings(
            project_root=Path(tmp_path),
            browser_enabled=False,
            terminal_enabled=False,
            memory_enabled=False,
            automation_enabled=False,
        )
    )
    window.show()
    app.processEvents()

    try:
        assert {
            "get_setting",
            "update_setting",
            "reset_setting",
            "list_supported_settings",
            "get_study_folder",
            "update_study_folder",
            "get_saved_location",
            "update_saved_location",
        } <= set(window.registry.list())
        ui_buttons = (
            window.home_button,
            window.chat_button,
            window.settings_button,
            window.voice_button,
            window.send_button,
            *window.home_page.findChildren(QPushButton, "homeAction"),
        )
        assert all(not button.icon().isNull() for button in ui_buttons)
        assert not window.home_page._background.isNull()
        assert window.pages.currentWidget() is window.home_page
        assert not window.sidebar.isVisible()
        assert not window.composer_frame.isVisible()

        home_actions = window.home_page.findChildren(QPushButton, "homeAction")
        QTest.mouseClick(home_actions[1], Qt.MouseButton.LeftButton)
        app.processEvents()
        assert window.pages.currentWidget() is window.chat_page
        assert window.sidebar.isVisible()
        assert window.composer_frame.isVisible()

        QTest.mouseClick(window.settings_button, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert window.pages.currentWidget() is window.settings_page
        assert window.sidebar.isVisible()
        assert not window.composer_frame.isVisible()
    finally:
        window.close()
        window.edge_overlay.close()
        app.processEvents()


def test_openrouter_settings_save_to_user_settings_and_update_provider(
    monkeypatch,
    tmp_path,
):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        FlexEdgeOverlay,
        "_install_global_mouse_hook",
        lambda _self: None,
    )
    stored_settings = QSettings("FLEX", "FLEX")
    keys = ("openrouter_api_key", "openrouter_model")
    previous_values = {
        key: (stored_settings.contains(key), stored_settings.value(key))
        for key in keys
    }
    window = FlexMainWindow(
        Settings(
            project_root=Path(tmp_path),
            openrouter_api_key="initial-test-key",
            openrouter_model="provider/initial-model",
            browser_enabled=False,
            terminal_enabled=False,
            memory_enabled=False,
            automation_enabled=False,
        )
    )

    try:
        assert window.api_key_input.echoMode() == QLineEdit.EchoMode.Password
        window.model_input.setText("provider/updated-model")
        window.api_key_input.setText("updated-test-key")
        window.save_openrouter_button.click()
        app.processEvents()

        assert window.provider.model == "provider/updated-model"
        assert window.provider.api_key == "updated-test-key"
        assert stored_settings.value("openrouter_model") == "provider/updated-model"
        assert stored_settings.value("openrouter_api_key") == "updated-test-key"
        assert window.openrouter_save_status.text() == "OpenRouter settings saved."
    finally:
        for key, (was_present, value) in previous_values.items():
            if was_present:
                stored_settings.setValue(key, value)
            else:
                stored_settings.remove(key)
        stored_settings.sync()
        window.close()
        window.edge_overlay.close()
        app.processEvents()


def test_discord_agent_isolated_and_public_agent_has_no_pc_tools(
    monkeypatch,
    tmp_path,
):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        FlexEdgeOverlay,
        "_install_global_mouse_hook",
        lambda _self: None,
    )

    class FakeProvider:
        def __init__(self):
            self.offered_tools = []

        def chat(self, _messages, *, tools=None):
            self.offered_tools.append(tools or [])
            return AIResponse(content="Hello from FLEX.")

        def supports_tools(self):
            return True

    window = FlexMainWindow(
        Settings(
            project_root=Path(tmp_path),
            browser_enabled=False,
            terminal_enabled=True,
            memory_enabled=True,
            automation_enabled=True,
        )
    )
    provider = FakeProvider()
    window.provider = provider

    try:
        assert window._process_discord_request("100", "dm:100", "hello") == "Hello from FLEX."
        public_agent = window._discord_agents["dm:100:authorized=False"]
        assert public_agent.registry.list() == []
        assert provider.offered_tools[-1] == []

        executed = []
        window.registry.register(
            ToolSpec(
                name="test_remote_control",
                description="Test remote control tool.",
                schema={"type": "object", "properties": {}},
                permission_level=PermissionLevel.LOW_RISK,
                executor=lambda: executed.append(True) or {"success": True},
            )
        )
        window.discord_manager.activate_user("100", "authorized-user")
        app.processEvents()
        assert (
            window._process_discord_request("100", "dm:100", "open chrome")
            == "Hello from FLEX."
        )
        authorized_agent = window._discord_agents["dm:100:authorized=True"]
        tool_names = set(authorized_agent.registry.list())
        assert "open_application" in tool_names
        assert "test_remote_control" in tool_names
        assert not any(name.startswith("terminal_") for name in tool_names)
        assert not any(name.startswith("memory_") for name in tool_names)
        assert "run_automation" not in tool_names
        assert authorized_agent.registry.get("test_remote_control").executor() == {
            "success": True
        }
        assert executed == [True]

        window.discord_manager.remove_user("100")
        app.processEvents()
        denied = authorized_agent.registry.get("test_remote_control").executor()
        assert denied["success"] is False
        assert denied["error"]["code"] == "DISCORD_ACCESS_REVOKED"
        assert executed == [True]

        window.discord_manager.activate_user("100", "authorized-user")
        app.processEvents()
        window._process_discord_request(
            "100",
            "guild:1:channel:2:user:100",
            "hello",
        )
        server_agent = window._discord_agents[
            "guild:1:channel:2:user:100:authorized=True"
        ]
        assert server_agent is not authorized_agent
    finally:
        window.close()
        window.edge_overlay.close()
        app.processEvents()


def test_discord_settings_securely_save_credentials_and_enable_integration(
    monkeypatch,
    tmp_path,
):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        FlexEdgeOverlay,
        "_install_global_mouse_hook",
        lambda _self: None,
    )
    monkeypatch.setattr(
        DiscordManager,
        "connect",
        lambda self: self._set_status("Connecting"),
    )
    stored_settings = QSettings("FLEX", "FLEX")
    keys = (
        "discord_bot_token",
        "discord_activation_password_hash",
        "discord_prefix",
        "discord_enabled",
    )
    previous_values = {
        key: (stored_settings.contains(key), stored_settings.value(key))
        for key in keys
    }
    window = FlexMainWindow(
        Settings(
            project_root=Path(tmp_path),
            browser_enabled=False,
            terminal_enabled=False,
            memory_enabled=False,
            automation_enabled=False,
        )
    )

    try:
        window.discord_token_input.setText("test-discord-token")
        window.discord_password_input.setText("test-activation-password")
        window.discord_prefix_input.setText("!flex")
        window._save_discord_settings()
        app.processEvents()

        assert stored_settings.value("discord_bot_token").startswith("dpapi:v1:")
        assert "test-discord-token" not in stored_settings.value("discord_bot_token")
        saved_hash = stored_settings.value("discord_activation_password_hash")
        assert verify_password("test-activation-password", saved_hash)
        assert stored_settings.value("discord_prefix") == "!flex"
        assert stored_settings.value("discord_enabled", type=bool)
        assert window.discord_status_label.text() == "Connecting"
        assert "!flex question" in window.discord_commands_text.text()
        window._disconnect_discord()
        assert not stored_settings.value("discord_enabled", type=bool)
        assert stored_settings.value("discord_bot_token").startswith("dpapi:v1:")
        assert verify_password(
            "test-activation-password",
            stored_settings.value("discord_activation_password_hash"),
        )
    finally:
        for key, (was_present, value) in previous_values.items():
            if was_present:
                stored_settings.setValue(key, value)
            else:
                stored_settings.remove(key)
        stored_settings.sync()
        window.close()
        window.edge_overlay.close()
        app.processEvents()


def test_about_flex_shows_creator_and_clickable_official_links(
    monkeypatch,
    tmp_path,
):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        FlexEdgeOverlay,
        "_install_global_mouse_hook",
        lambda _self: None,
    )
    window = FlexMainWindow(
        Settings(
            project_root=Path(tmp_path),
            browser_enabled=False,
            terminal_enabled=False,
            memory_enabled=False,
            automation_enabled=False,
        )
    )

    try:
        assert "FLEX" in window.about_identity_label.text()
        assert "Ayman Khan" in window.about_creator_label.text()
        assert window.about_creator_links.openExternalLinks()
        assert "https://aymanweb.vercel.app" in window.about_creator_links.text()
        assert "https://aymanbio.vercel.app" in window.about_creator_links.text()
        assert "https://github.com/AYMAN-KHAN-000" in window.about_creator_links.text()
    finally:
        window.close()
        window.edge_overlay.close()
        app.processEvents()


def test_microphone_button_stays_checked_for_active_voice_chat(
    monkeypatch,
    tmp_path,
):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        FlexEdgeOverlay,
        "_install_global_mouse_hook",
        lambda _self: None,
    )
    monkeypatch.setattr(FlexMainWindow, "_start_voice_listener", lambda _self: None)
    window = FlexMainWindow(
        Settings(
            project_root=Path(tmp_path),
            browser_enabled=False,
            terminal_enabled=False,
            memory_enabled=False,
            automation_enabled=False,
        )
    )

    try:
        window._show_page(1)
        window.voice_button.click()
        app.processEvents()

        assert window._voice_chat_enabled
        assert window.voice_button.isChecked()
        assert "border: 3px solid #a749e8;" in window.styleSheet()
    finally:
        window.close()
        window.edge_overlay.close()
        app.processEvents()


def test_recommended_research_page_uses_configured_browser(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        FlexEdgeOverlay,
        "_install_global_mouse_hook",
        lambda _self: None,
    )
    window = FlexMainWindow(
        Settings(
            project_root=Path(tmp_path),
            browser_default="edge",
            browser_enabled=False,
            terminal_enabled=False,
            memory_enabled=False,
            automation_enabled=False,
        )
    )
    opened = {}
    tool = window.registry.get("open_website")
    monkeypatch.setattr(
        tool,
        "executor",
        lambda **arguments: (
            opened.update(arguments)
            or {
                "status": "opened",
                "url": arguments["website"],
            }
        ),
    )
    try:
        result = window._open_research_page(
            "https://example.org/product",
            browser="chrome",
        )

        assert result["status"] == "opened"
        assert opened == {
            "website": "https://example.org/product",
            "browser": "chrome",
        }
    finally:
        window.close()
        window.edge_overlay.close()
        app.processEvents()


def test_settings_manage_saved_and_usage_memories(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        FlexEdgeOverlay,
        "_install_global_mouse_hook",
        lambda _self: None,
    )
    monkeypatch.setattr(
        OPEN_APPLICATION_TOOL,
        "executor",
        lambda application: (
            {"status": "not_found", "application": application}
            if application == "Missing"
            else {
                "status": "opened",
                "application": application,
                "path": str(tmp_path / f"{application}.exe"),
            }
        ),
    )
    stored_settings = QSettings("FLEX", "FLEX")
    keys = (
        "usage_tracking_enabled",
        "usage_application_tracking_enabled",
        "usage_website_tracking_enabled",
        "usage_media_tracking_enabled",
        "study_folders",
        "smart_research_enabled",
        "weather_enabled",
    )
    previous_values = {
        key: (stored_settings.contains(key), stored_settings.value(key))
        for key in keys
    }
    window = FlexMainWindow(
        Settings(
            project_root=Path(tmp_path),
            browser_enabled=False,
            terminal_enabled=False,
            memory_enabled=False,
            automation_enabled=False,
        )
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    try:
        window.registry.get("open_application").executor(application="Notepad")
        window.registry.get("open_application").executor(application="Notepad")
        window.registry.get("open_application").executor(application="Missing")
        tracked_app = window.usage_memory.search("Notepad")[0]
        assert tracked_app["open_count"] == 2
        assert all(item["name"] != "Missing" for item in window.usage_memory.list_items())
        window.usage_memory.clear()

        memory = window.memory_database.save_memory(
            "preference",
            "preferred_editor",
            "VS Code",
        )["memory"]
        window._refresh_saved_memories_list()
        assert window.saved_memories_list.count() == 1
        window.saved_memories_list.setCurrentRow(0)
        window._delete_selected_saved_memory()
        with pytest.raises(KeyError):
            window.memory_database.get_memory(memory_id=memory["id"])

        usage = window.usage_memory.record_open(
            item_type="pdf",
            name="Notes.pdf",
            location=str(tmp_path / "Notes.pdf"),
        )
        window._refresh_usage_memory_list()
        assert window.usage_memory_list.count() == 1
        window.usage_memory_list.setCurrentRow(0)
        window._delete_selected_usage_item()
        with pytest.raises(KeyError):
            window.usage_memory.delete_item(usage["id"])

        window.usage_tracking_checkbox.setChecked(False)
        assert window.usage_tracking_enabled is False
        assert stored_settings.value("usage_tracking_enabled", type=bool) is False
        window.registry.get("open_application").executor(application="Browser")
        assert all(
            item["name"] != "Browser"
            for item in window.usage_memory.list_items()
        )
    finally:
        for key, (was_present, value) in previous_values.items():
            if was_present:
                stored_settings.setValue(key, value)
            else:
                stored_settings.remove(key)
        stored_settings.sync()
        window.close()
        window.edge_overlay.close()
        app.processEvents()


def test_settings_manage_research_weather_and_saved_location(
    monkeypatch,
    tmp_path,
):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        FlexEdgeOverlay,
        "_install_global_mouse_hook",
        lambda _self: None,
    )
    stored_settings = QSettings("FLEX", "FLEX")
    keys = ("smart_research_enabled", "weather_enabled")
    previous_values = {
        key: (stored_settings.contains(key), stored_settings.value(key))
        for key in keys
    }
    window = FlexMainWindow(
        Settings(
            project_root=Path(tmp_path),
            browser_enabled=False,
            terminal_enabled=False,
            memory_enabled=False,
            automation_enabled=False,
        )
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    try:
        assert "research_web" in window.registry
        assert "get_weather_forecast" in window.registry
        window.smart_research_checkbox.setChecked(False)
        assert "research_web" not in window.registry
        window.smart_research_checkbox.setChecked(True)
        assert "research_web" in window.registry
        window.weather_enabled_checkbox.setChecked(False)
        assert "get_weather_forecast" not in window.registry
        window.weather_enabled_checkbox.setChecked(True)
        assert "get_weather_forecast" in window.registry

        window.location_city_input.setText("Dhaka")
        window.location_country_input.setText("Bangladesh")
        window.location_timezone_input.setText("Asia/Dhaka")
        window._save_location_settings()
        assert "Confirm the location" in window.location_status.text()
        window.confirm_location_checkbox.setChecked(True)
        window._save_location_settings()
        assert window.location_manager.get().city == "Dhaka"

        window.research_history.save(
            "keyboard under budget",
            "product",
            [{"title": "Keyboard", "url": "https://example.org/item"}],
            "source snippet",
            selected_result={
                "title": "Keyboard",
                "url": "https://example.org/item",
            },
        )
        window._refresh_research_history()
        assert window.research_history_list.count() == 1
        window._clear_research_history()
        assert window.research_history.list() == []

        window._delete_saved_location()
        assert window.location_manager.get() is None
    finally:
        for key, (was_present, value) in previous_values.items():
            if was_present:
                stored_settings.setValue(key, value)
            else:
                stored_settings.remove(key)
        stored_settings.sync()
        window.close()
        window.edge_overlay.close()
        app.processEvents()
