from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import Event, RLock
from urllib.parse import urlsplit

from PySide6.QtCore import QDateTime, QSettings, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QDateTimeEdit,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from flex.ai.openrouter import OpenRouterProvider
from flex.automation import AutomationRunner, create_automation_tool
from flex.automation.storage import AutomationStore
from flex.config import Settings
from flex.context.context_tools import create_context_tools
from flex.context.location_manager import LocationManager
from flex.context.weather_service import WeatherService
from flex.core.agent import FLEXAgent
from flex.discord import DiscordManager
from flex.memory.database import MemoryDatabase
from flex.memory.tools import create_memory_tools
from flex.memory.usage_memory import UsageMemory, classify_path
from flex.research.research_engine import ResearchEngine
from flex.research.research_history import ResearchHistory
from flex.research.research_tools import create_research_tools
from flex.research.search_provider import SearchProvider
from flex.security.permissions import PermissionLevel, PermissionManager
from flex.security.secret_store import protect_secret, unprotect_secret
from flex.settings.settings_manager import SettingsManager
from flex.settings.settings_tools import create_settings_tools
from flex.tools.app_control import (
    CLOSE_APPLICATION_TOOL,
    FIND_APPLICATION_TOOL,
    LIST_RUNNING_APPLICATIONS_TOOL,
    OPEN_APPLICATION_TOOL,
    open_application_executor,
)
from flex.tools.browser import BrowserManager, create_browser_tools
from flex.tools.desktop import (
    OPEN_SOCIAL_ACCOUNT_TOOL,
    OPEN_WEBSITE_TOOL,
    SEARCH_WEB_TOOL,
    TYPE_TEXT_TOOL,
)
from flex.tools.filesystem import (
    CREATE_FILE_TOOL,
    CREATE_FOLDER_TOOL,
    LIST_DIRECTORY_TOOL,
    OPEN_FILE_TOOL,
    SEARCH_FILES_TOOL,
)
from flex.tools.file_search import create_file_search_tools
from flex.tools.personal import create_personal_tools
from flex.tools.registry import ToolRegistry
from flex.tasks.task_manager import TaskManager, TaskStorageError
from flex.tasks.task_tools import create_task_manager_tool
from flex.tools.terminal import SafeTerminal, create_terminal_tools
from flex.tools.system_control import OPEN_WINDOWS_UI_TOOL, WINDOWS_POWER_ACTION_TOOL
from flex.tools.windows_quick_controls import create_windows_quick_control_tools
from flex.tools.windows_utilities import (
    WindowsUtilityTools,
    create_windows_utility_tools,
)
from flex.ui.app_icon import load_application_icon
from flex.ui.chat_renderer import ChatResponseBrowser
from flex.ui.edge_overlay import FlexEdgeOverlay
from flex.voice import (
    SpeechRecognitionWorker,
    speak_text,
)

VOICE_IDLE_TIMEOUT_MS = 20_000
HAPPY_EXPRESSION_DURATION_MS = 4_000
LOVE_EXPRESSION_DURATION_MS = 5_000
NOTCH_EXPRESSION_TEST_COMMAND = "natural9090-test"
NOTCH_EXPRESSION_IDS = frozenset(
    {"neutral", "happy", "sleep", "love", "wonder", "confused"}
)
logger = logging.getLogger(__name__)

_LOVE_MESSAGE_PATTERN = re.compile(
    r"\b(?:love\s+you|i\s+(?:really\s+)?love\b|adore\s+you|i\s+adore\b|"
    r"i\s+(?:really\s+)?(?:care|appreciate)\s+about\s+you|"
    r"i\s+appreciate\s+you|i\s+cherish\s+you|i(?:'m| am)\s+fond\s+of\s+you|"
    r"sweetheart|sweetie|honey|darling|beloved|my\s+love|dear|"
    r"you're\s+(?:so\s+)?sweet|you\s+are\s+(?:so\s+)?sweet|"
    r"you're\s+lovely|you\s+are\s+lovely|you're\s+the\s+best|"
    r"you\s+are\s+the\s+best|xoxo|kisses\s+for\s+you)\b",
    re.IGNORECASE,
)
_WONDER_MESSAGE_PATTERN = re.compile(
    r"\b(?:wonderful|amazing|astonish(?:ing|ed)|shocking|shocked|surpris(?:ing|ed)|"
    r"unbelievable|incredible|mind\s+blowing|what\s+a\s+surprise|wow)\b",
    re.IGNORECASE,
)
_UNCLEAR_RESPONSE_PATTERN = re.compile(
    r"\b(?:"
    r"i\s+(?:could(?:n't| not)|can't|cannot|did(?:n't| not))\s+understand|"
    r"i\s+did(?:n't| not)\s+catch|"
    r"(?:i(?:'m| am)\s+)?not\s+sure\s+what\s+(?:you\s+)?(?:mean|said)|"
    r"(?:please|could\s+you|can\s+you)\s+(?:repeat|rephrase|clarify)|"
    r"(?:say|tell)\s+(?:that|me\s+that)\s+again|"
    r"clarify\s+your\s+(?:request|question)"
    r")\b",
    re.IGNORECASE,
)

_ADMIRATION_MESSAGE_PATTERN = re.compile(
    r"\b(?:nice\s+work|great\s+work|good\s+work|good\s+job|well\s+done|"
    r"you(?:'re| are)\s+(?:so\s+|really\s+)?(?:great|awesome|excellent|fantastic|"
    r"brilliant|helpful|the\s+best)|"
    r"you(?:'re| are)\s+(?:doing|being)\s+(?:so\s+)?(?:great|awesome|excellent|"
    r"fantastic|brilliant)|"
    r"you\s+(?:did|have\s+done|completed)\b.{0,100}\b(?:perfectly|perfect|parfect|"
    r"great|excellent|brilliantly)|"
    r"that\s+was\s+(?:so\s+)?(?:perfect|parfect|great|excellent|fantastic|brilliant))\b",
    re.IGNORECASE,
)
_STANDALONE_ADMIRATION = {
    "awesome",
    "brilliant",
    "excellent",
    "fantastic",
    "great",
    "nice",
    "parfect",
    "perfect",
    "perfectly",
}


def _message_expression(text: str) -> str | None:
    if _LOVE_MESSAGE_PATTERN.search(text):
        return "love"
    if _WONDER_MESSAGE_PATTERN.search(text):
        return "wonder"
    return None


def _is_unclear_response(text: str) -> bool:
    return bool(_UNCLEAR_RESPONSE_PATTERN.search(text))


def _make_ui_icon(name: str) -> QIcon:
    pixmap = QPixmap(96, 96)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(
        QPen(
            QColor("#ffffff"),
            7,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if name == "home":
        path = QPainterPath()
        path.moveTo(13, 46)
        path.lineTo(48, 16)
        path.lineTo(83, 46)
        path.moveTo(24, 42)
        path.lineTo(24, 80)
        path.lineTo(72, 80)
        path.lineTo(72, 42)
        path.moveTo(42, 80)
        path.lineTo(42, 59)
        path.quadTo(48, 51, 54, 59)
        path.lineTo(54, 80)
        painter.drawPath(path)
    elif name == "chat":
        path = QPainterPath()
        path.addRoundedRect(12, 14, 72, 54, 14, 14)
        path.moveTo(30, 67)
        path.lineTo(23, 82)
        path.lineTo(45, 68)
        painter.drawPath(path)
        painter.setBrush(QColor("#ffffff"))
        for x in (32, 48, 64):
            painter.drawEllipse(x - 4, 36, 8, 8)
    elif name == "settings":
        painter.setBrush(QColor("#ffffff"))
        painter.save()
        painter.translate(48, 48)
        for _ in range(8):
            painter.drawRoundedRect(-6, -43, 12, 24, 3, 3)
            painter.rotate(45)
        painter.restore()
        painter.drawEllipse(19, 19, 58, 58)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.drawEllipse(38, 38, 20, 20)
    elif name == "discord":
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(22, 18, 52, 52)
        painter.setBrush(QColor("#000000"))
        painter.drawEllipse(36, 32, 24, 24)
        painter.setBrush(QColor("#ffffff"))
        path = QPainterPath()
        path.moveTo(24, 48)
        path.lineTo(72, 48)
        path.lineTo(60, 72)
        path.lineTo(36, 72)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "microphone":
        painter.drawRoundedRect(34, 11, 28, 48, 14, 14)
        path = QPainterPath()
        path.moveTo(23, 45)
        path.cubicTo(23, 76, 73, 76, 73, 45)
        path.moveTo(48, 70)
        path.lineTo(48, 84)
        path.moveTo(35, 84)
        path.lineTo(61, 84)
        painter.drawPath(path)
    elif name == "send":
        path = QPainterPath()
        path.moveTo(13, 46)
        path.lineTo(84, 13)
        path.lineTo(63, 84)
        path.lineTo(47, 56)
        path.closeSubpath()
        path.moveTo(47, 56)
        path.lineTo(84, 13)
        painter.drawPath(path)
    else:
        painter.end()
        raise ValueError(f"Unknown FLEX UI icon: {name}")

    painter.end()
    return QIcon(pixmap)


class _ThemedPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        background_path = Path(__file__).parent / "assets" / "flex_background.jpg"
        self._background = QPixmap(str(background_path))
        if self._background.isNull():
            raise FileNotFoundError(f"Could not load FLEX page background: {background_path}")

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#100819"))
        painter.setOpacity(0.24)
        painter.drawPixmap(self.rect(), self._background)
        painter.setOpacity(1.0)
        painter.fillRect(self.rect(), QColor(7, 3, 12, 82))


def _is_gratitude_message(text: str) -> bool:
    if re.search(
        r"\b(?:thanks?|thank\s+you|appreciat(?:e|ed|ing)|grateful)\b",
        text.casefold(),
    ):
        return True
    normalized = " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())
    return (
        normalized in _STANDALONE_ADMIRATION
        or _ADMIRATION_MESSAGE_PATTERN.search(text) is not None
    )


def _voice_notch_command(text: str) -> str | None:
    normalized = " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())
    if re.search(r"\b(?:pc|computer|windows|laptop|desktop|device|system)\b", normalized):
        return None

    command = re.sub(r"^(?:hey\s+)?flex\s+", "", normalized)
    command = re.sub(r"\s+flex$", "", command)
    command = re.sub(r"^(?:please\s+)", "", command)
    command = re.sub(r"(?:\s+please)$", "", command)
    if command in {
        "sleep",
        "to sleep",
        "go sleep",
        "go to sleep",
        "sleep mode",
        "go sleep mode",
        "go to sleep mode",
        "enter sleep mode",
        "turn on sleep mode",
        "activate sleep mode",
    }:
        return "sleep"
    if command in {
        "wake",
        "wake up",
        "wake flex",
        "wake up flex",
        "get up",
    }:
        return "wake"
    return None


@dataclass
class ApprovalPrompt:
    tool_name: str
    details: str
    answered: Event = field(default_factory=Event)
    approved: bool = False


class RequestWorker(QThread):
    response_ready = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, agent: FLEXAgent, request: str, speak_response: bool) -> None:
        super().__init__()
        self.agent = agent
        self.request = request
        self.speak_response = speak_response

    def run(self) -> None:
        try:
            voice_request = getattr(self.agent, "process_voice_request", None)
            if self.speak_response and callable(voice_request):
                response = voice_request(self.request)
            else:
                response = self.agent.process_request(self.request)
        except Exception as exc:
            self.error_occurred.emit(str(exc))
            if self.speak_response:
                try:
                    speak_text("I couldn't complete that request.")
                except Exception as speech_exc:
                    self.error_occurred.emit(
                        f"Voice playback failed: {speech_exc}"
                    )
            return

        self.response_ready.emit(response)
        if self.speak_response:
            try:
                speak_text(response)
            except Exception as exc:
                self.error_occurred.emit(f"Voice playback failed: {exc}")


class FlexMainWindow(QMainWindow):
    approval_requested = Signal(object)
    research_status_changed = Signal(str)
    settings_changed = Signal(str, object)
    discord_status_changed = Signal(str)
    discord_users_changed = Signal()

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.application_settings = QSettings("FLEX", "FLEX")
        self.settings_changed.connect(self._on_setting_changed)
        self.discord_status_changed.connect(self._update_discord_status)
        self.discord_users_changed.connect(self._on_discord_users_changed)
        self._discord_agents_lock = RLock()
        self._discord_agents: dict[str, FLEXAgent] = {}
        self._discord_agent_locks: dict[str, RLock] = {}
        self.smart_research_enabled = self.application_settings.value(
            "smart_research_enabled",
            True,
            type=bool,
        )
        self.weather_enabled = self.application_settings.value(
            "weather_enabled",
            True,
            type=bool,
        )
        self.usage_tracking_enabled = self.application_settings.value(
            "usage_tracking_enabled",
            True,
            type=bool,
        )
        self.usage_application_tracking_enabled = self.application_settings.value(
            "usage_application_tracking_enabled",
            True,
            type=bool,
        )
        self.usage_website_tracking_enabled = self.application_settings.value(
            "usage_website_tracking_enabled",
            True,
            type=bool,
        )
        self.usage_media_tracking_enabled = self.application_settings.value(
            "usage_media_tracking_enabled",
            True,
            type=bool,
        )
        self.study_folders = self.application_settings.value(
            "study_folders",
            [],
            type=list,
        )
        self.settings.openrouter_api_key = str(
            self.application_settings.value(
                "openrouter_api_key",
                self.settings.openrouter_api_key,
            )
        )
        self.settings.openrouter_model = str(
            self.application_settings.value(
                "openrouter_model",
                self.settings.openrouter_model,
            )
        )
        self.settings.clipboard_access_enabled = self.application_settings.value(
            "clipboard_access_enabled",
            self.settings.clipboard_access_enabled,
            type=bool,
        )
        self.settings.screenshot_directory = str(
            self.application_settings.value(
                "screenshot_directory",
                self.settings.screenshot_directory,
            )
        )
        self.settings.file_search_locations = self.application_settings.value(
            "file_search_locations",
            self.settings.file_search_locations,
            type=list,
        )
        stored_discord_token = str(
            self.application_settings.value(
                "discord_bot_token",
                self.settings.discord_bot_token,
            )
        )
        try:
            self.settings.discord_bot_token = unprotect_secret(stored_discord_token)
            if (
                self.settings.discord_bot_token
                and not stored_discord_token.startswith("dpapi:v1:")
            ):
                self.application_settings.setValue(
                    "discord_bot_token",
                    protect_secret(self.settings.discord_bot_token),
                )
        except (OSError, ValueError):
            logger.exception("Could not unprotect the saved Discord bot token.")
            self.settings.discord_bot_token = ""
            self.settings.discord_status = "Connection Error"
        self.settings.discord_activation_password_hash = str(
            self.application_settings.value(
                "discord_activation_password_hash",
                self.settings.discord_activation_password_hash,
            )
        )
        self.settings.discord_prefix = str(
            self.application_settings.value(
                "discord_prefix",
                self.settings.discord_prefix,
            )
        )
        self.discord_integration_enabled = self.application_settings.value(
            "discord_enabled",
            False,
            type=bool,
        )
        self.file_search_roots = list(self.settings.file_search_locations)
        personal_db_path = Path(self.settings.memory_db_path).expanduser()
        if not personal_db_path.is_absolute():
            personal_db_path = self.settings.project_root / personal_db_path
        self.discord_manager = DiscordManager(
            personal_db_path.parent.parent,
            token=self.settings.discord_bot_token,
            prefix=self.settings.discord_prefix,
            status_callback=self.discord_status_changed.emit,
            users_changed_callback=self.discord_users_changed.emit,
        )
        self.discord_manager.activation_password = self.settings.discord_activation_password_hash
        self.windows_utility_tools = WindowsUtilityTools(self.settings)
        self.memory_database = MemoryDatabase(personal_db_path)
        self.usage_memory = UsageMemory(personal_db_path)
        self.location_manager = LocationManager(personal_db_path)
        self.weather_service = WeatherService(personal_db_path)
        self.settings_manager = SettingsManager(
            settings_factory=lambda: QSettings("FLEX", "FLEX"),
            locations=self.location_manager,
            weather=self.weather_service,
            search_locations=self.file_search_roots,
            configured_search_locations=self.settings.file_search_locations,
            on_change=lambda name, value: self.settings_changed.emit(name, value),
        )
        task_timezone = (
            self.settings_manager.get_setting("preferred_timezone") or "Asia/Dhaka"
        )
        task_storage_root = settings.project_root
        if getattr(sys, "frozen", False):
            local_app_data = os.environ.get("LOCALAPPDATA")
            task_storage_root = (
                Path(local_app_data) / "FLEX"
                if local_app_data
                else Path.home() / "AppData" / "Local" / "FLEX"
            )
        try:
            self.task_manager = TaskManager(
                task_storage_root / "tasks.json",
                default_timezone=str(task_timezone),
            )
        except ValueError:
            logger.exception(
                "The saved preferred timezone is invalid; using Asia/Dhaka for tasks."
            )
            self.task_manager = TaskManager(task_storage_root / "tasks.json")
        self.task_notifications_enabled = self.application_settings.value(
            "task_notifications_enabled", True, type=bool
        )
        self.task_start_notifications_enabled = self.application_settings.value(
            "task_start_notifications_enabled", True, type=bool
        )
        self.task_end_notifications_enabled = self.application_settings.value(
            "task_end_notifications_enabled", True, type=bool
        )
        self.task_notification_sound_enabled = self.application_settings.value(
            "task_notification_sound_enabled", False, type=bool
        )
        self.task_auto_cleanup_enabled = self.application_settings.value(
            "task_auto_cleanup_enabled", True, type=bool
        )
        self.task_notification_duration_ms = self.application_settings.value(
            "task_notification_duration_ms", 7_000, type=int
        )
        self.task_notification_duration_ms = max(
            3_000,
            min(60_000, self.task_notification_duration_ms),
        )
        self.research_history = ResearchHistory(personal_db_path)
        self.search_provider = SearchProvider()
        self.research_engine = ResearchEngine(
            self.search_provider,
            self.research_history,
            status_callback=self.research_status_changed.emit,
        )
        self.personal_tools = create_personal_tools(personal_db_path)
        self.file_search_tools = create_file_search_tools(self.file_search_roots)
        self.windows_quick_control_tools = create_windows_quick_control_tools()
        self.setWindowTitle("FLEX")
        self.setWindowIcon(load_application_icon())
        self.resize(1080, 760)
        self.setMinimumSize(900, 600)
        self.setStyleSheet(
            """
            QWidget {
                background-color: transparent;
                color: #ffffff;
                font-family: "Segoe UI";
                font-size: 10pt;
            }
            QWidget#appRoot {
                background-color: #100819;
            }
            QFrame#sidebar {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #12091d, stop:1 #55168a);
                border-right: 1px solid #8e55ad;
            }
            QLabel#brandName {
                font-family: "Impact", "Bahnschrift SemiCondensed", "Segoe UI";
                font-size: 27pt;
                font-weight: 900;
                letter-spacing: 2px;
            }
            QLabel#muted, QLabel#pageSubtitle {
                color: #d3c4e3;
            }
            QPushButton#navButton {
                min-height: 60px;
                padding: 0 8px;
                text-align: center;
                color: white;
                background-color: #050407;
                border: 2px solid #dedbe2;
                border-radius: 22px;
                font-family: "Impact", "Bahnschrift SemiCondensed", "Segoe UI";
                font-size: 20pt;
                font-weight: 700;
            }
            QPushButton#navButton:hover {
                background-color: #24112f;
                border-color: #ffffff;
            }
            QPushButton#navButton:checked {
                color: #ffffff;
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #160b20, stop:1 #741db2);
                border: 2px solid #ffffff;
            }
            QPushButton#sendButton, QPushButton#voiceButton {
                min-width: 54px;
                min-height: 54px;
                color: white;
                font-size: 18pt;
                font-weight: 700;
                background-color: #09070c;
                border: 2px solid #dedbe2;
                border-radius: 27px;
            }
            QPushButton#sendButton:hover, QPushButton#voiceButton:hover {
                background-color: #321143;
                border-color: #ffffff;
            }
            QPushButton#voiceButton:checked {
                color: white;
                background-color: #170b20;
                border: 3px solid #a749e8;
            }
            QLineEdit#settingsInput {
                min-height: 38px;
                padding: 0 12px;
                color: #ffffff;
                background-color: #09050e;
                border: 1px solid #744295;
                border-radius: 10px;
                selection-background-color: #8126bd;
            }
            QLineEdit#settingsInput:focus {
                border: 2px solid #a749e8;
            }
            QLineEdit#messageInput {
                min-height: 58px;
                padding: 0 24px;
                color: #ffffff;
                background-color: #050407;
                border: 2px solid #dedbe2;
                border-radius: 30px;
                selection-background-color: #8126bd;
            }
            QLineEdit#messageInput:focus {
                border-color: #a749e8;
            }
            QFrame#settingsCard {
                background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #100718, stop:0.55 #1e0a2e, stop:1 #100718);
                border: 2px solid #71339a;
                border-radius: 24px;
            }
            QFrame#homeActions {
                background-color: transparent;
            }
            QPushButton#homeAction {
                min-height: 76px;
                color: #ffffff;
                background-color: #050407;
                border: 2px solid #ffffff;
                border-radius: 30px;
                font-family: "Impact", "Bahnschrift SemiCondensed", "Segoe UI";
                font-size: 25pt;
                font-weight: 700;
            }
            QPushButton#homeAction:hover {
                background-color: #37134e;
            }
            QLabel#chatStatus {
                min-height: 40px;
                padding: 0 18px;
                background-color: #050407;
                border: 2px solid #dedbe2;
                border-radius: 20px;
                font-weight: 600;
            }
            QPushButton {
                color: #ffffff;
                background-color: #281037;
                border: 1px solid #7d44a0;
                border-radius: 12px;
                padding: 8px 14px;
            }
            QPushButton:hover {
                background-color: #471666;
                border-color: #b865e9;
            }
            QCheckBox {
                spacing: 9px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border: 1px solid #ab7bc7;
                border-radius: 5px;
                background-color: #0d0712;
            }
            QCheckBox::indicator:checked {
                background-color: #9a35dd;
                border-color: #c783f2;
            }
            """
        )

        central = QWidget(self)
        central.setObjectName("appRoot")
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        root_layout.addLayout(body, 1)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(206)
        self.sidebar = sidebar
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(18, 30, 18, 18)
        sidebar_layout.setSpacing(14)
        brand_icon = QLabel()
        brand_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_icon.setPixmap(
            QPixmap(str(Path(__file__).parent / "assets" / "flex_icon.png")).scaled(
                86,
                86,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        sidebar_layout.addWidget(brand_icon)
        brand = QLabel("FLEX")
        brand.setObjectName("brandName")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sidebar_layout.addWidget(brand)
        sidebar_layout.addSpacing(12)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("chatStatus")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.research_status_changed.connect(self.status_label.setText)
        self._navigation_group = QButtonGroup(self)
        self._navigation_group.setExclusive(True)
        self.home_button = self._make_navigation_button("home", "Home")
        self.chat_button = self._make_navigation_button("chat", "Chat")
        self.discord_button = self._make_navigation_button("discord", "DISCORD")
        self.settings_button = self._make_navigation_button("settings", "Settings")
        for index, button in enumerate((self.home_button, self.chat_button, self.discord_button, self.settings_button)):
            self._navigation_group.addButton(button, index)
            sidebar_layout.addWidget(button)
        sidebar_layout.addStretch(1)
        body.addWidget(sidebar)

        self.pages = QStackedWidget()
        self.home_page = self._make_home_page()
        self.chat_page = self._make_chat_page()
        self.discord_page = self._make_discord_page()
        self.settings_page = self._make_settings_page()
        self.pages.addWidget(self.home_page)
        self.pages.addWidget(self.chat_page)
        self.pages.addWidget(self.discord_page)
        self.pages.addWidget(self.settings_page)
        body.addWidget(self.pages, 1)
        self.home_button.clicked.connect(lambda: self._show_page(0))
        self.chat_button.clicked.connect(lambda: self._show_page(1))
        self.discord_button.clicked.connect(lambda: self._show_page(2))
        self.settings_button.clicked.connect(lambda: self._show_page(3))
        self.home_button.setChecked(True)

        self.composer_frame = QFrame()
        self.composer_frame.setObjectName("composerFrame")
        self.composer_frame.setStyleSheet("background-color: transparent;")
        composer_layout = QHBoxLayout(self.composer_frame)
        composer_layout.setContentsMargins(0, 12, 0, 24)
        composer_layout.setSpacing(10)
        self.input_box = QLineEdit()
        self.input_box.setObjectName("messageInput")
        self.input_box.setPlaceholderText("TYPE MESSAGE...")
        self.input_box.returnPressed.connect(self._handle_submit)
        self.input_box.setMinimumWidth(180)
        composer_layout.addWidget(self.input_box, 1)

        self.voice_button = QPushButton()
        self.voice_button.setObjectName("voiceButton")
        self.voice_button.setIcon(_make_ui_icon("microphone"))
        self.voice_button.setIconSize(QSize(29, 29))
        self.voice_button.setCheckable(True)
        self.voice_button.setToolTip("Start or stop voice chat")
        self.voice_button.setAccessibleName("Start voice chat")
        self.voice_button.clicked.connect(self._toggle_voice_chat)
        composer_layout.addWidget(self.voice_button)

        self.cancel_button = QPushButton("Stop")
        self.cancel_button.setToolTip("Stop the current request or automation")
        self.cancel_button.clicked.connect(self._cancel_request)
        self.cancel_button.setVisible(False)
        composer_layout.addWidget(self.cancel_button)

        self.send_button = QPushButton()
        self.send_button.setObjectName("sendButton")
        self.send_button.setIcon(_make_ui_icon("send"))
        self.send_button.setIconSize(QSize(28, 28))
        self.send_button.setToolTip("Send message (Enter)")
        self.send_button.setAccessibleName("Send message")
        self.send_button.clicked.connect(self._handle_submit)
        composer_layout.addWidget(self.send_button)
        self.chat_page.layout().insertWidget(
            0,
            self.status_label,
            alignment=Qt.AlignmentFlag.AlignRight,
        )
        self.chat_page.layout().addWidget(self.composer_frame)
        self.sidebar.setVisible(False)
        self.composer_frame.setVisible(False)

        self.registry = ToolRegistry()
        self.registry.register(OPEN_APPLICATION_TOOL)
        self.registry.register(CLOSE_APPLICATION_TOOL)
        self.registry.register(FIND_APPLICATION_TOOL)
        self.registry.register(LIST_RUNNING_APPLICATIONS_TOOL)
        self.registry.register(SEARCH_WEB_TOOL)
        self.registry.register(OPEN_WEBSITE_TOOL)
        self.registry.register(OPEN_SOCIAL_ACCOUNT_TOOL)
        self.registry.register(TYPE_TEXT_TOOL)
        self.registry.register(CREATE_FILE_TOOL)
        self.registry.register(OPEN_FILE_TOOL)
        self.registry.register(CREATE_FOLDER_TOOL)
        self.registry.register(LIST_DIRECTORY_TOOL)
        self.registry.register(SEARCH_FILES_TOOL)
        self.registry.register(OPEN_WINDOWS_UI_TOOL)
        self.registry.register(WINDOWS_POWER_ACTION_TOOL)
        self.registry.register(create_task_manager_tool(self.task_manager))
        for utility_tool in create_windows_utility_tools(
            self.settings,
            self.windows_utility_tools,
        ):
            self.registry.register(utility_tool)
        for utility_tool in self.file_search_tools:
            self.registry.register(utility_tool)
        for utility_tool in self.personal_tools:
            self.registry.register(utility_tool)
        for utility_tool in self.windows_quick_control_tools:
            self.registry.register(utility_tool)
        self.context_tool_specs = create_context_tools(
            self.location_manager,
            self.weather_service,
            weather_enabled=True,
            settings_manager=self.settings_manager,
            open_weather_app=lambda: open_application_executor("Weather"),
        )
        for context_tool in self.context_tool_specs:
            if context_tool.name != "get_weather_forecast" or self.weather_enabled:
                self.registry.register(context_tool)
        for settings_tool in create_settings_tools(self.settings_manager):
            self.registry.register(settings_tool)
        if self.smart_research_enabled:
            self._register_research_tools()
        self.browser_manager: BrowserManager | None = None
        if settings.browser_enabled:
            self.browser_manager = BrowserManager(
                timeout_ms=settings.browser_timeout_ms,
                max_tabs=settings.browser_max_tabs,
                default_browser=settings.browser_default,
            )
            for browser_tool in create_browser_tools(self.browser_manager):
                self.registry.register(browser_tool)
        self.terminal: SafeTerminal | None = None
        if settings.terminal_enabled:
            self.terminal = SafeTerminal(
                settings.project_root,
                timeout_ms=settings.terminal_timeout_ms,
                max_output=settings.terminal_max_output,
            )
            for terminal_tool in create_terminal_tools(self.terminal):
                self.registry.register(terminal_tool)
        if settings.memory_enabled:
            for memory_tool in create_memory_tools(
                str(personal_db_path),
                enabled=True,
                require_confirmation=settings.memory_confirmation,
            ):
                self.registry.register(memory_tool)
        self._install_usage_tracking()
        self.approval_requested.connect(self._handle_approval_request)

        self.provider = OpenRouterProvider(
            api_key=settings.openrouter_api_key,
            model=settings.openrouter_model,
        )
        self.permission_manager = PermissionManager(
            require_confirmation=settings.require_confirmation,
            confirmation_callback=self._request_tool_approval,
        )
        self.agent = FLEXAgent(
            provider=self.provider,
            registry=self.registry,
            permission_manager=self.permission_manager,
        )
        if self.terminal is not None:
            self.terminal.cancel_event = self.agent.cancellation_event
        if settings.automation_enabled:
            self.automation_runner = AutomationRunner(
                self.registry,
                self.permission_manager,
                AutomationStore(personal_db_path),
                enabled=True,
                max_steps=settings.automation_max_steps,
                max_runtime_seconds=settings.automation_max_runtime_seconds,
                max_retries=settings.automation_max_retries,
                default_tool_timeout_ms=settings.default_tool_timeout_ms,
            )
            self.registry.register(
                create_automation_tool(
                    self.automation_runner,
                    self.agent.cancellation_event,
                )
            )
        self.discord_manager.set_request_handler(self._process_discord_request)

        self._voice_chat_enabled = False
        self._speech_worker: SpeechRecognitionWorker | None = None
        self._request_worker: RequestWorker | None = None
        self._voice_idle_timer = QTimer(self)
        self._voice_idle_timer.setSingleShot(True)
        self._voice_idle_timer.setInterval(VOICE_IDLE_TIMEOUT_MS)
        self._voice_idle_timer.timeout.connect(self._handle_voice_idle_timeout)
        self._happy_expression_timer = QTimer(self)
        self._happy_expression_timer.setSingleShot(True)
        self._happy_expression_timer.setInterval(HAPPY_EXPRESSION_DURATION_MS)
        self._happy_expression_timer.timeout.connect(self._restore_notch_expression)
        self._task_scheduler_timer = QTimer(self)
        self._task_scheduler_timer.setInterval(15_000)
        self._task_scheduler_timer.timeout.connect(self._check_due_tasks)

        self.edge_overlay = FlexEdgeOverlay(self.settings)
        self.edge_overlay.send_requested.connect(self._handle_overlay_submit)
        self.edge_overlay.voice_input_toggled.connect(
            self._set_voice_chat_enabled
        )
        self.edge_overlay.wake_requested.connect(self._handle_notch_wake)
        self._voice_notch_sleeping = False
        self._setup_system_tray()
        app = QApplication.instance()
        if app is not None:
            if self.browser_manager is not None:
                app.aboutToQuit.connect(self.browser_manager.close)
            app.aboutToQuit.connect(self.discord_manager.disconnect)
        self._refresh_task_list()
        self._task_scheduler_timer.start()
        self._check_due_tasks()

        self._append_response("FLEX is ready. Type a request to get started.")
        if self.discord_integration_enabled and self.discord_manager.token:
            self.discord_manager.connect()

    def _restore_notch_expression(self) -> None:
        if self._voice_notch_sleeping and self._voice_chat_enabled:
            expression = "sleep"
        else:
            expression = "neutral"
        self.edge_overlay.set_expression(expression)

    def _show_timed_expression(self, expression: str, duration_ms: int) -> None:
        if self._voice_notch_sleeping and self._voice_chat_enabled:
            return
        self._voice_notch_sleeping = False
        self._happy_expression_timer.stop()
        self.edge_overlay.set_expression(expression)
        self._happy_expression_timer.start(duration_ms)

    def _show_love_expression(self) -> None:
        self._show_timed_expression("love", LOVE_EXPRESSION_DURATION_MS)

    def _show_wonder_expression(self) -> None:
        self._show_timed_expression("wonder", HAPPY_EXPRESSION_DURATION_MS)

    def _show_confused_expression(self) -> None:
        self._show_timed_expression("confused", HAPPY_EXPRESSION_DURATION_MS)

    def _set_clipboard_access(self, enabled: bool) -> None:
        self.settings.clipboard_access_enabled = enabled
        self.windows_utility_tools.clipboard_access_enabled = enabled
        self.application_settings.setValue("clipboard_access_enabled", enabled)

    def _choose_screenshot_directory(self) -> None:
        initial_directory = (
            self.settings.screenshot_directory
            or str(Path.home() / "Pictures" / "FLEX Screenshots")
        )
        directory = QFileDialog.getExistingDirectory(
            self,
            "Choose screenshot folder",
            initial_directory,
        )
        if not directory:
            return

        self.settings.screenshot_directory = directory
        self.windows_utility_tools.screenshot_directory = directory
        self.application_settings.setValue("screenshot_directory", directory)
        self.screenshot_directory_setting.setText(f"Screenshot folder: {directory}")

    def _add_file_search_location(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Add a folder to file search",
            str(Path.home()),
        )
        if not directory:
            return
        resolved = str(Path(directory).resolve())
        if any(
            Path(existing).resolve() == Path(resolved)
            for existing in self.settings.file_search_locations
        ):
            return
        self.settings.file_search_locations.append(resolved)
        if resolved not in self.file_search_roots:
            self.file_search_roots.append(resolved)
        self.application_settings.setValue(
            "file_search_locations",
            self.settings.file_search_locations,
        )
        self.file_search_locations_setting.setText(
            "Additional file search folders: "
            + ", ".join(self.settings.file_search_locations)
        )

    def _remove_file_search_location(self) -> None:
        locations = self.settings.file_search_locations
        if not locations:
            return
        location, accepted = QInputDialog.getItem(
            self,
            "Remove file search folder",
            "Choose a folder to stop searching:",
            locations,
            0,
            False,
        )
        if not accepted:
            return
        self.settings.file_search_locations.remove(location)
        normalized = os.path.normcase(os.path.abspath(location))
        self.file_search_roots[:] = [
            path
            for path in self.file_search_roots
            if os.path.normcase(os.path.abspath(path)) != normalized
        ]
        self.application_settings.setValue("file_search_locations", locations)
        self.file_search_locations_setting.setText(
            "Additional file search folders: "
            + (", ".join(locations) or "None")
        )

    def _make_navigation_button(self, icon: str, label: str) -> QPushButton:
        button = QPushButton(label)
        button.setObjectName("navButton")
        button.setIcon(_make_ui_icon(icon))
        button.setIconSize(QSize(27, 27))
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _make_home_page(self) -> QWidget:
        page = _ThemedPage()
        page.setObjectName("homePage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(70, 38, 70, 62)
        layout.setSpacing(0)
        layout.addStretch(1)

        hero = QVBoxLayout()
        hero.setSpacing(10)
        hero.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        logo = QLabel()
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setPixmap(
            QPixmap(str(Path(__file__).parent / "assets" / "flex_icon.png")).scaled(
                142,
                142,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        hero.addWidget(logo)

        title = QLabel("FLEX")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            'font-family: "Impact", "Bahnschrift SemiCondensed", "Segoe UI";'
            "font-size: 82pt; font-weight: 900; letter-spacing: 1px;"
        )
        hero.addWidget(title)

        subtitle = QLabel("Your Personal AI Desktop Assistant")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet("font-size: 14pt;")
        hero.addWidget(subtitle)
        creator_credit = QLabel("Created & Developed by Ayman Khan")
        creator_credit.setObjectName("muted")
        creator_credit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hero.addWidget(creator_credit)
        layout.addLayout(hero)

        layout.addStretch(2)
        actions = QHBoxLayout()
        actions.setSpacing(24)
        for icon, label, page_index in (
            ("home", "Home", 0),
            ("chat", "Chat", 1),
            ("settings", "Settings", 3),
        ):
            button = QPushButton(label)
            button.setObjectName("homeAction")
            button.setIcon(_make_ui_icon(icon))
            button.setIconSize(QSize(38, 38))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, index=page_index: self._show_page(index)
            )
            actions.addWidget(button, 1)
        layout.addLayout(actions)
        return page

    def _install_usage_tracking(self) -> None:
        tracked_tools = {
            "open_application",
            "open_website",
            "open_social_account",
            "browser_open",
            "browser_navigate",
            "browser_search",
            "browser_back",
            "browser_refresh",
            "open_file",
            "open_found_file",
            "open_file_location",
            "open_quick_folder",
        }
        for tool in self.registry:
            if tool.name not in tracked_tools:
                continue
            executor = tool.executor
            verifier = tool.verification
            name = tool.name

            def execute_and_record(
                _executor=executor,
                _verifier=verifier,
                _name=name,
                **arguments,
            ):
                result = _executor(**arguments)
                verified = (
                    _verifier(arguments, result)
                    if _verifier is not None
                    else result.get("success") is True
                    or result.get("status") == "opened"
                )
                if (
                    self.usage_tracking_enabled
                    and verified
                    and self._usage_tracking_allowed(_name, arguments, result)
                ):
                    try:
                        self._record_usage(_name, arguments, result)
                    except (OSError, sqlite3.Error, ValueError):
                        logger.exception("Could not save local usage memory for %s.", _name)
                return result

            tool.executor = execute_and_record

    def _usage_tracking_allowed(
        self,
        tool_name: str,
        arguments: dict[str, object],
        result: dict[str, object],
    ) -> bool:
        if tool_name == "open_application":
            return self.usage_application_tracking_enabled
        if tool_name in {
            "open_website",
            "open_social_account",
            "browser_open",
            "browser_navigate",
            "browser_search",
            "browser_back",
            "browser_refresh",
        }:
            return self.usage_website_tracking_enabled
        path = str(result.get("path") or arguments.get("path") or "")
        if path and classify_path(path) in {"video", "audio"}:
            return self.usage_media_tracking_enabled
        return True

    def _record_usage(
        self,
        tool_name: str,
        arguments: dict[str, object],
        result: dict[str, object],
    ) -> None:
        if tool_name == "open_application":
            name = str(result.get("application") or arguments.get("application") or "")
            location = str(result.get("path") or name)
            normalized_name = name.casefold()
            browsers = {
                "brave",
                "chrome",
                "chromium",
                "edge",
                "firefox",
                "google chrome",
                "microsoft edge",
                "mozilla firefox",
                "opera",
            }
            self.usage_memory.record_open(
                item_type="application",
                name=name,
                location=location,
                application=name,
                category="browser" if normalized_name in browsers else "application",
            )
            return

        website_tools = {
            "open_website",
            "open_social_account",
            "browser_open",
            "browser_navigate",
            "browser_search",
            "browser_back",
            "browser_refresh",
        }
        if tool_name in website_tools:
            url = str(result.get("url") or arguments.get("url") or "")
            if not url:
                return
            host = urlsplit(url).hostname or url
            self.usage_memory.record_open(
                item_type="website",
                name=host,
                location=url,
                application=str(result.get("browser") or arguments.get("browser") or "FLEX browser"),
                category="website",
            )
            return

        path = str(result.get("path") or arguments.get("path") or "")
        if not path:
            return
        item_type = classify_path(path)
        resolved = Path(path).expanduser().resolve(strict=False)
        category = None
        if item_type in {"pdf", "document"}:
            for folder in self.study_folders:
                try:
                    if resolved.is_relative_to(Path(folder).expanduser().resolve(strict=False)):
                        category = "study"
                        break
                except (OSError, ValueError):
                    continue
        application = str(result.get("application") or arguments.get("application") or "")
        self.usage_memory.record_open(
            item_type=item_type,
            name=resolved.name or str(resolved),
            location=str(resolved),
            application=application or None,
            category=category,
        )

    def _make_chat_page(self) -> QWidget:
        page = _ThemedPage()
        page.setObjectName("chatPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(42, 18, 42, 10)
        layout.setSpacing(8)

        self.chat_output = ChatResponseBrowser()
        self.chat_output.setObjectName("chatOutput")
        self.chat_output.setPlaceholderText("Your conversation will appear here.")
        layout.addWidget(self.chat_output, 1)
        return page

    def _make_discord_page(self) -> QWidget:
        page = _ThemedPage()
        page.setObjectName("discordPage")
        page.setStyleSheet(
            """
            QWidget#discordPage {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #120714, stop:1 #1a0d2e);
            }
            QFrame#discordFieldPanel {
                background-color: #b547e8;
                border: 2px solid #f3f3f3;
                border-radius: 18px;
                min-height: 54px;
            }
            QFrame#discordPanel {
                background-color: #b547e8;
                border: 2px solid #f3f3f3;
                border-radius: 18px;
            }
            QLabel#discordLabel { color: #ffffff; font-size: 20pt; font-weight: 800; }
            QLineEdit#discordInput {
                background: transparent; border: none; color: #ffffff; font-size: 18pt; font-weight: 700;
            }
            QPushButton#discordAction {
                background-color: #c35ce9; border: 2px solid #f3f3f3; border-radius: 18px; min-height: 58px; font-size: 18pt; font-weight: 800; color: white;
            }
            QPushButton#discordAction:hover { background-color: #d17ef3; }
            QPushButton#discordSmallToggle {
                background: transparent; border: none; color: white; font-size: 22pt; font-weight: 800;
            }
            QLabel#discordPanelTitle { color: white; font-size: 22pt; font-weight: 800; }
            QLabel#discordPanelSubtitle { color: #f1dafe; font-size: 12pt; }
            QLabel#discordPanelList { color: white; font-size: 18pt; }
            """
        )
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setSpacing(18)

        self.discord_token_input = QLineEdit(self.settings.discord_bot_token)
        self.discord_token_input.setObjectName("discordInput")
        self.discord_token_input.setPlaceholderText("BOT TOKEN")
        self.discord_token_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.discord_token_input.setClearButtonEnabled(False)
        token_panel = QFrame()
        token_panel.setObjectName("discordFieldPanel")
        token_layout = QHBoxLayout(token_panel)
        token_layout.setContentsMargins(18, 8, 12, 8)
        token_label = QLabel("TOKEN:")
        token_label.setObjectName("discordLabel")
        token_label.setMinimumWidth(120)
        self.discord_toggle_token = QPushButton("◉")
        self.discord_toggle_token.setObjectName("discordSmallToggle")
        self.discord_toggle_token.clicked.connect(
            lambda: self.discord_token_input.setEchoMode(
                QLineEdit.EchoMode.Normal if self.discord_token_input.echoMode() == QLineEdit.EchoMode.Password else QLineEdit.EchoMode.Password
            )
        )
        token_layout.addWidget(token_label)
        token_layout.addWidget(self.discord_token_input, 1)
        token_layout.addWidget(self.discord_toggle_token)

        self.discord_password_input = QLineEdit()
        self.discord_password_input.setObjectName("discordInput")
        self.discord_password_input.setPlaceholderText("ACTIVATION PASSWORD")
        self.discord_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.discord_password_input.setClearButtonEnabled(False)
        password_panel = QFrame()
        password_panel.setObjectName("discordFieldPanel")
        password_layout = QHBoxLayout(password_panel)
        password_layout.setContentsMargins(18, 8, 12, 8)
        password_label = QLabel("PASSWORD:")
        password_label.setObjectName("discordLabel")
        password_label.setMinimumWidth(120)
        self.discord_toggle_password = QPushButton("◉")
        self.discord_toggle_password.setObjectName("discordSmallToggle")
        self.discord_toggle_password.clicked.connect(
            lambda: self.discord_password_input.setEchoMode(
                QLineEdit.EchoMode.Normal if self.discord_password_input.echoMode() == QLineEdit.EchoMode.Password else QLineEdit.EchoMode.Password
            )
        )
        password_layout.addWidget(password_label)
        password_layout.addWidget(self.discord_password_input, 1)
        password_layout.addWidget(self.discord_toggle_password)

        self.discord_prefix_input = QLineEdit(self.settings.discord_prefix or "-flex")
        self.discord_prefix_input.setObjectName("discordInput")
        prefix_panel = QFrame()
        prefix_panel.setObjectName("discordFieldPanel")
        prefix_layout = QHBoxLayout(prefix_panel)
        prefix_layout.setContentsMargins(18, 8, 12, 8)
        prefix_label = QLabel("PREFIX:")
        prefix_label.setObjectName("discordLabel")
        prefix_label.setMinimumWidth(120)
        prefix_layout.addWidget(prefix_label)
        prefix_layout.addWidget(self.discord_prefix_input, 1)

        layout.addWidget(token_panel)
        layout.addWidget(password_panel)
        layout.addWidget(prefix_panel)

        action_row = QHBoxLayout()
        self.discord_save_button = QPushButton("SAVE bot info")
        self.discord_save_button.setObjectName("discordAction")
        self.discord_save_button.clicked.connect(self._save_discord_settings)
        self.discord_reset_button = QPushButton("Reset/delete info")
        self.discord_reset_button.setObjectName("discordAction")
        self.discord_reset_button.clicked.connect(self._reset_discord_settings)
        action_row.addWidget(self.discord_save_button, 1)
        action_row.addWidget(self.discord_reset_button, 1)
        layout.addLayout(action_row)

        status_row = QHBoxLayout()
        status_label = QLabel("Connection status:")
        status_label.setStyleSheet("color: white; font-size: 14pt; font-weight: 700;")
        self.discord_status_label = QLabel(self.settings.discord_status or "Disconnected")
        self.discord_status_label.setStyleSheet("color: white; font-size: 14pt; font-weight: 700;")
        status_row.addWidget(status_label)
        status_row.addWidget(self.discord_status_label)
        status_row.addStretch(1)
        self.discord_disconnect_button = QPushButton("Disconnect bot")
        self.discord_disconnect_button.clicked.connect(self._disconnect_discord)
        status_row.addWidget(self.discord_disconnect_button)
        self.discord_activity_button = QPushButton("Recent activity")
        self.discord_activity_button.clicked.connect(self._view_discord_activity)
        status_row.addWidget(self.discord_activity_button)
        layout.addLayout(status_row)

        panels = QHBoxLayout()
        panels.setSpacing(18)
        activated_panel = QFrame()
        activated_panel.setObjectName("discordPanel")
        activated_layout = QVBoxLayout(activated_panel)
        activated_layout.setContentsMargins(18, 18, 18, 18)
        activated_title = QLabel("ACTIVATED USERS")
        activated_title.setObjectName("discordPanelTitle")
        activated_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        activated_subtitle = QLabel("People activated pc access by password, their user names")
        activated_subtitle.setObjectName("discordPanelSubtitle")
        activated_subtitle.setWordWrap(True)
        activated_subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.discord_user_list = QListWidget()
        self.discord_user_list.setStyleSheet("background: transparent; border: none; color: white; font-size: 18pt;")
        self.discord_user_list.setMaximumHeight(200)
        self._refresh_discord_users()
        activated_layout.addWidget(activated_title)
        activated_layout.addWidget(activated_subtitle)
        activated_layout.addWidget(self.discord_user_list)

        commands_panel = QFrame()
        commands_panel.setObjectName("discordPanel")
        commands_layout = QVBoxLayout(commands_panel)
        commands_layout.setContentsMargins(18, 18, 18, 18)
        commands_title = QLabel("COMMANDS")
        commands_title.setObjectName("discordPanelTitle")
        commands_title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        commands_text = QLabel(
            f"/guide\n/flex-activate\n{self.discord_manager.prefix} question\n"
            "anything or control\npc if your account is\nconnected by\nactivation command"
        )
        self.discord_commands_text = commands_text
        commands_text.setObjectName("discordPanelList")
        commands_text.setWordWrap(True)
        commands_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        commands_layout.addWidget(commands_title)
        commands_layout.addWidget(commands_text)

        panels.addWidget(activated_panel, 1)
        panels.addWidget(commands_panel, 1)
        layout.addLayout(panels)
        return page

    def _save_discord_settings(self) -> None:
        token = self.discord_token_input.text().strip() or self.settings.discord_bot_token
        password = self.discord_password_input.text().strip()
        prefix = (self.discord_prefix_input.text() or "-flex").strip() or "-flex"
        try:
            protected_token = protect_secret(token)
        except OSError:
            logger.exception("Could not protect the Discord bot token for storage.")
            QMessageBox.critical(
                self,
                "Discord settings",
                "FLEX could not securely store the Discord bot token.",
            )
            return
        if self.discord_manager.disconnect() != "Disconnected":
            QMessageBox.critical(
                self,
                "Discord settings",
                "FLEX could not stop the current Discord connection. Settings were not changed.",
            )
            return
        self.settings.discord_bot_token = token
        self.settings.discord_prefix = prefix
        if password:
            from flex.discord.manager import hash_password
            self.settings.discord_activation_password_hash = hash_password(password)
        self.application_settings.setValue("discord_bot_token", protected_token)
        self.application_settings.setValue("discord_prefix", prefix)
        self.application_settings.setValue("discord_enabled", True)
        if password:
            self.application_settings.setValue("discord_activation_password_hash", self.settings.discord_activation_password_hash)
        self.discord_manager.token = token
        self.discord_manager.prefix = prefix
        self.discord_manager.activation_password = self.settings.discord_activation_password_hash
        self.discord_integration_enabled = True
        self.discord_manager.connect()
        self.settings.discord_status = self.discord_manager.get_status()
        self.discord_commands_text.setText(
            f"/guide\n/flex-activate\n{prefix} question\n"
            "anything or control\npc if your account is\nconnected by\nactivation command"
        )
        self.discord_password_input.clear()
        self._refresh_discord_users()

    def _reset_discord_settings(self) -> None:
        answer = QMessageBox.question(
            self,
            "Reset Discord integration",
            "Disconnect the bot and delete its saved credentials and activated users?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.discord_manager.reset_configuration()
        except RuntimeError:
            logger.exception("Could not reset the Discord integration.")
            QMessageBox.critical(
                self,
                "Discord settings",
                "FLEX could not stop the Discord connection. Saved settings were not deleted.",
            )
            return
        self.discord_token_input.clear()
        self.discord_password_input.clear()
        self.discord_prefix_input.setText("-flex")
        self.settings.discord_bot_token = ""
        self.settings.discord_activation_password_hash = ""
        self.settings.discord_prefix = "-flex"
        self.settings.discord_status = "Disconnected"
        self.discord_integration_enabled = False
        self.application_settings.remove("discord_bot_token")
        self.application_settings.remove("discord_activation_password_hash")
        self.application_settings.remove("discord_prefix")
        self.application_settings.remove("discord_enabled")
        self.discord_commands_text.setText(
            "/guide\n/flex-activate\n-flex question\n"
            "anything or control\npc if your account is\nconnected by\nactivation command"
        )
        self.discord_status_label.setText("Disconnected")
        self._refresh_discord_users()

    def _refresh_discord_users(self) -> None:
        if not hasattr(self, "discord_user_list"):
            return
        self.discord_user_list.clear()
        for user in self.discord_manager.list_users():
            item = QListWidgetItem()
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(2, 2, 2, 2)
            details = QLabel(
                f"@{user.username or user.discord_id}\n"
                f"ID: {user.discord_id}  |  {user.activation_date[:10]}  |  {user.status}"
            )
            details.setWordWrap(True)
            remove_button = QPushButton("REMOVE")
            remove_button.setFixedWidth(92)
            remove_button.clicked.connect(
                lambda _checked=False, user_id=user.discord_id: self._remove_discord_user(user_id)
            )
            row_layout.addWidget(details, 1)
            row_layout.addWidget(remove_button)
            item.setSizeHint(row.sizeHint())
            self.discord_user_list.addItem(item)
            self.discord_user_list.setItemWidget(item, row)

    def _remove_discord_user(self, discord_user_id: str) -> None:
        answer = QMessageBox.question(
            self,
            "Remove Discord user",
            (
                "Revoke FLEX PC access for this Discord account?\n\n"
                f"Discord user ID: {discord_user_id}\n\n"
                "The account will retain public AI access."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.discord_manager.remove_user(discord_user_id)

    def _view_discord_activity(self) -> None:
        activity = self.discord_manager.recent_activity
        message = "\n".join(activity[:20]) if activity else "No Discord activity yet."
        QMessageBox.information(
            self,
            "Recent Discord activity",
            message,
        )

    def _disconnect_discord(self) -> None:
        if self.discord_manager.disconnect() != "Disconnected":
            QMessageBox.critical(
                self,
                "Discord settings",
                "FLEX could not stop the Discord connection.",
            )
            return
        self.discord_integration_enabled = False
        self.application_settings.setValue("discord_enabled", False)
        self.settings.discord_status = "Disconnected"

    def _on_discord_users_changed(self) -> None:
        with self._discord_agents_lock:
            self._discord_agents.clear()
            self._discord_agent_locks.clear()
        self._refresh_discord_users()

    def _process_discord_request(
        self,
        discord_user_id: str,
        context_key: str,
        request: str,
    ) -> str:
        authorized = self.discord_manager.is_active_user(discord_user_id)
        agent_key = f"{context_key}:authorized={authorized}"
        with self._discord_agents_lock:
            agent = self._discord_agents.get(agent_key)
            if agent is None:
                scoped_registry = ToolRegistry()
                if authorized:
                    blocked_tools = {
                        "clipboard_control",
                        "manage_tasks",
                        "run_automation",
                        "screenshot",
                    }
                    for tool in self.registry:
                        if (
                            tool.name in blocked_tools
                            or tool.name.startswith(("terminal_", "memory_"))
                            or tool.name.startswith(("update_", "reset_"))
                        ):
                            continue
                        permission = tool.permission_level
                        if any(
                            marker in tool.name
                            for marker in (
                                "delete",
                                "remove",
                                "move",
                                "rename",
                                "shutdown",
                                "restart",
                                "sign_out",
                                "browser_click",
                                "browser_type",
                            )
                        ):
                            permission = PermissionLevel.HIGH_RISK
                        original_executor = tool.executor
                        def make_guarded_executor(executor, user_id):
                            def guarded_executor(**arguments):
                                if not self.discord_manager.is_active_user(user_id):
                                    return {
                                        "success": False,
                                        "error": {
                                            "code": "DISCORD_ACCESS_REVOKED",
                                            "message": "FLEX PC access was revoked.",
                                            "retryable": False,
                                        },
                                    }
                                return executor(**arguments)

                            return guarded_executor

                        scoped_registry.register(
                            replace(
                                tool,
                                permission_level=permission,
                                executor=make_guarded_executor(
                                    original_executor,
                                    discord_user_id,
                                ),
                            )
                        )
                remote_permissions = PermissionManager(
                    require_confirmation=True,
                    confirmation_callback=self._request_tool_approval,
                )
                agent = FLEXAgent(
                    provider=self.provider,
                    registry=scoped_registry,
                    permission_manager=remote_permissions,
                )
                self._discord_agents[agent_key] = agent
                self._discord_agent_locks[agent_key] = RLock()
            request_lock = self._discord_agent_locks[agent_key]

        with request_lock:
            if len(agent.context.messages) > 40:
                del agent.context.messages[:-40]
            return agent.process_request(request)

    def _make_settings_page(self) -> QWidget:
        page = _ThemedPage()
        page.setObjectName("settingsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 18, 28, 20)
        layout.setSpacing(12)

        heading = QHBoxLayout()
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.setSpacing(16)
        logo = QLabel()
        logo.setPixmap(
            QPixmap(str(Path(__file__).parent / "assets" / "flex_icon.png")).scaled(
                104,
                104,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        heading.addWidget(logo)
        title = QLabel("SETTINGS")
        title.setStyleSheet(
            'font-family: "Impact", "Bahnschrift SemiCondensed", "Segoe UI";'
            "font-size: 48pt; font-weight: 900;"
        )
        heading.addWidget(title)
        layout.addLayout(heading)

        subtitle = QLabel(
            "Your FLEX connection, privacy, and creator information."
        )
        subtitle.setObjectName("pageSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        card = QFrame()
        card.setObjectName("settingsCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 14, 18, 14)
        card_layout.setSpacing(8)
        model_label = QLabel("OpenRouter model")
        self.model_input = QLineEdit(self.settings.openrouter_model)
        self.model_input.setObjectName("settingsInput")
        self.model_input.setPlaceholderText("provider/model-name")
        api_key_label = QLabel("OpenRouter API key")
        self.api_key_input = QLineEdit(self.settings.openrouter_api_key)
        self.api_key_input.setObjectName("settingsInput")
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("Paste your OpenRouter API key")
        self.show_api_key_checkbox = QCheckBox("Show API key")
        self.show_api_key_checkbox.toggled.connect(
            lambda visible: self.api_key_input.setEchoMode(
                QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
            )
        )
        self.save_openrouter_button = QPushButton("Save OpenRouter settings")
        self.save_openrouter_button.setObjectName("saveOpenRouterButton")
        self.save_openrouter_button.clicked.connect(self._save_openrouter_settings)
        self.openrouter_save_status = QLabel("")
        self.openrouter_save_status.setObjectName("muted")
        confirmation_setting = QLabel(
            "Action confirmations: On" if self.settings.require_confirmation else "Action confirmations: Off"
        )
        self.clipboard_access_checkbox = QCheckBox(
            "Allow FLEX to access the clipboard when I ask"
        )
        self.clipboard_access_checkbox.setChecked(self.settings.clipboard_access_enabled)
        self.clipboard_access_checkbox.setToolTip(
            "FLEX does not monitor or save clipboard history. Clipboard tools are only used on request."
        )
        self.clipboard_access_checkbox.toggled.connect(self._set_clipboard_access)
        self.screenshot_directory_setting = QLabel(
            "Screenshot folder: "
            + (
                self.settings.screenshot_directory
                or str(Path.home() / "Pictures" / "FLEX Screenshots")
            )
        )
        self.screenshot_directory_setting.setWordWrap(True)
        self.choose_screenshot_directory_button = QPushButton("Change screenshot folder")
        self.choose_screenshot_directory_button.clicked.connect(
            self._choose_screenshot_directory
        )
        self.file_search_locations_setting = QLabel(
            "Additional file search folders: "
            + (", ".join(self.settings.file_search_locations) or "None")
        )
        self.file_search_locations_setting.setWordWrap(True)
        self.study_folder_setting = QLabel(
            "Preferred study folder: "
            + (self.settings_manager.get_setting("study_folder") or "Not set")
        )
        self.study_folder_setting.setWordWrap(True)
        self.add_file_search_location_button = QPushButton("Add file search folder")
        self.add_file_search_location_button.clicked.connect(self._add_file_search_location)
        self.remove_file_search_location_button = QPushButton("Remove file search folder")
        self.remove_file_search_location_button.clicked.connect(
            self._remove_file_search_location
        )
        self.usage_tracking_checkbox = QCheckBox("Track items opened through FLEX")
        self.usage_tracking_checkbox.setChecked(self.usage_tracking_enabled)
        self.usage_tracking_checkbox.setToolTip(
            "Stores item names, locations, and successful FLEX opens locally in flex.db. "
            "Usage history is not sent to OpenRouter."
        )
        self.usage_tracking_checkbox.toggled.connect(self._set_usage_tracking)
        self.application_tracking_checkbox = QCheckBox("Track applications opened through FLEX")
        self.application_tracking_checkbox.setChecked(self.usage_application_tracking_enabled)
        self.application_tracking_checkbox.toggled.connect(
            lambda enabled: self._set_usage_category_tracking("application", enabled)
        )
        self.website_tracking_checkbox = QCheckBox("Track websites opened through FLEX")
        self.website_tracking_checkbox.setChecked(self.usage_website_tracking_enabled)
        self.website_tracking_checkbox.toggled.connect(
            lambda enabled: self._set_usage_category_tracking("website", enabled)
        )
        self.media_tracking_checkbox = QCheckBox("Track media opened through FLEX")
        self.media_tracking_checkbox.setChecked(self.usage_media_tracking_enabled)
        self.media_tracking_checkbox.toggled.connect(
            lambda enabled: self._set_usage_category_tracking("media", enabled)
        )
        self.smart_research_checkbox = QCheckBox("Enable Smart Research")
        self.smart_research_checkbox.setChecked(self.smart_research_enabled)
        self.smart_research_checkbox.toggled.connect(self._set_smart_research_enabled)
        self.research_history_list = QListWidget()
        self.research_history_list.setObjectName("researchHistoryList")
        self.research_history_list.setMaximumHeight(120)
        self.refresh_research_history_button = QPushButton("Refresh research history")
        self.refresh_research_history_button.clicked.connect(self._refresh_research_history)
        self.clear_research_history_button = QPushButton("Clear research history")
        self.clear_research_history_button.clicked.connect(self._clear_research_history)
        self.research_history_status = QLabel("")
        self.research_history_status.setObjectName("muted")
        self.weather_enabled_checkbox = QCheckBox("Enable weather lookup")
        self.weather_enabled_checkbox.setChecked(self.weather_enabled)
        self.weather_enabled_checkbox.toggled.connect(self._set_weather_enabled)
        saved_location = self.location_manager.get()
        self.location_city_input = QLineEdit(saved_location.city if saved_location else "")
        self.location_city_input.setPlaceholderText("City or area")
        self.location_country_input = QLineEdit(saved_location.country if saved_location else "")
        self.location_country_input.setPlaceholderText("Country")
        self.location_timezone_input = QLineEdit(
            saved_location.timezone if saved_location else ""
        )
        self.location_timezone_input.setPlaceholderText("IANA timezone, e.g. Asia/Dhaka")
        self.confirm_location_checkbox = QCheckBox(
            "I confirm saving this preferred city, country, and timezone"
        )
        self.save_location_button = QPushButton("Save preferred location")
        self.save_location_button.clicked.connect(self._save_location_settings)
        self.delete_location_button = QPushButton("Delete saved location")
        self.delete_location_button.clicked.connect(self._delete_saved_location)
        self.clear_weather_cache_button = QPushButton("Clear cached weather data")
        self.clear_weather_cache_button.clicked.connect(self._clear_weather_cache)
        self.location_status = QLabel(
            "Saved location: "
            + (
                f"{saved_location.city}, {saved_location.country} ({saved_location.timezone})"
                if saved_location
                else "Not set"
            )
        )
        self.location_status.setObjectName("muted")
        self.location_status.setWordWrap(True)
        self.preferred_timezone_setting = QLabel(
            "Preferred timezone: "
            + (
                self.settings_manager.get_setting("preferred_timezone")
                or "Use the system timezone unless a location is saved"
            )
        )
        self.preferred_timezone_setting.setWordWrap(True)
        self.usage_memory_privacy_note = QLabel(
            "Personal Usage Memory records successful opens initiated by FLEX and only indexes "
            "documents in study folders you select. It never reads file contents, monitors your "
            "PC, or reads browser history. Usage records stay in the local database and are not "
            "provided to OpenRouter."
        )
        self.usage_memory_privacy_note.setObjectName("muted")
        self.usage_memory_privacy_note.setWordWrap(True)
        self.study_folders_setting = QLabel(
            "Study folders: " + (", ".join(self.study_folders) or "None")
        )
        self.study_folders_setting.setWordWrap(True)
        self.add_study_folder_button = QPushButton("Add study folder")
        self.add_study_folder_button.clicked.connect(self._add_study_folder)
        self.remove_study_folder_button = QPushButton("Remove study folder")
        self.remove_study_folder_button.clicked.connect(self._remove_study_folder)
        self.usage_memory_list = QListWidget()
        self.usage_memory_list.setObjectName("usageMemoryList")
        self.usage_memory_list.setMaximumHeight(150)
        self.usage_memory_search_input = QLineEdit()
        self.usage_memory_search_input.setPlaceholderText("Search local usage items")
        self.usage_memory_search_input.returnPressed.connect(self._refresh_usage_memory_list)
        self.usage_memory_sort = QComboBox()
        self.usage_memory_sort.addItem("Most recent", "recent")
        self.usage_memory_sort.addItem("Most frequent", "frequent")
        self.usage_memory_sort.currentIndexChanged.connect(
            self._handle_usage_memory_sort_changed
        )
        self.refresh_usage_memory_button = QPushButton("Refresh usage items")
        self.refresh_usage_memory_button.clicked.connect(self._refresh_usage_memory_list)
        self.delete_usage_memory_button = QPushButton("Remove selected usage item")
        self.delete_usage_memory_button.clicked.connect(self._delete_selected_usage_item)
        self.clear_usage_memory_button = QPushButton("Clear usage history")
        self.clear_usage_memory_button.clicked.connect(self._clear_usage_memory)
        self.usage_memory_status = QLabel("")
        self.usage_memory_status.setObjectName("muted")
        self.saved_memories_list = QListWidget()
        self.saved_memories_list.setObjectName("savedMemoriesList")
        self.saved_memories_list.setMaximumHeight(130)
        self.delete_saved_memory_button = QPushButton("Remove selected saved memory")
        self.delete_saved_memory_button.clicked.connect(self._delete_selected_saved_memory)
        self.saved_memory_status = QLabel("")
        self.saved_memory_status.setObjectName("muted")
        self._refresh_usage_memory_list()
        self._refresh_saved_memories_list()
        self._refresh_research_history()
        voice_setting = QLabel(
            "Voice input: English transcription runs locally with the "
            "faster-whisper small.en model; microphone audio is not uploaded. "
            "The model downloads the first time voice chat is used. Spoken "
            "replies use Windows text-to-speech."
        )
        automation_setting = QLabel(
            "Browser: "
            + ("On" if self.settings.browser_enabled else "Off")
            + f" ({self.settings.browser_default}, {self.settings.browser_timeout_ms} ms); "
            + "Terminal: "
            + ("On" if self.settings.terminal_enabled else "Off")
            + f" ({self.settings.terminal_timeout_ms} ms); Memory: "
            + ("On" if self.settings.memory_enabled else "Off")
            + "; Automations: "
            + ("On" if self.settings.automation_enabled else "Off")
        )
        for label in (
            confirmation_setting,
            voice_setting,
            automation_setting,
        ):
            label.setWordWrap(True)
            card_layout.addWidget(label)
        card_layout.addWidget(model_label)
        card_layout.addWidget(self.model_input)
        card_layout.addWidget(api_key_label)
        card_layout.addWidget(self.api_key_input)
        card_layout.addWidget(self.show_api_key_checkbox)
        card_layout.addWidget(self.save_openrouter_button)
        card_layout.addWidget(self.openrouter_save_status)
        card_layout.addWidget(self.clipboard_access_checkbox)
        card_layout.addWidget(self.screenshot_directory_setting)
        card_layout.addWidget(self.choose_screenshot_directory_button)
        card_layout.addWidget(self.file_search_locations_setting)
        card_layout.addWidget(self.add_file_search_location_button)
        card_layout.addWidget(self.remove_file_search_location_button)
        card_layout.addWidget(self.study_folder_setting)
        card_layout.addWidget(self.usage_tracking_checkbox)
        card_layout.addWidget(self.application_tracking_checkbox)
        card_layout.addWidget(self.website_tracking_checkbox)
        card_layout.addWidget(self.media_tracking_checkbox)
        card_layout.addWidget(self.smart_research_checkbox)
        card_layout.addWidget(QLabel("Recent research history"))
        card_layout.addWidget(self.research_history_list)
        card_layout.addWidget(self.refresh_research_history_button)
        card_layout.addWidget(self.clear_research_history_button)
        card_layout.addWidget(self.research_history_status)
        card_layout.addWidget(self.weather_enabled_checkbox)
        card_layout.addWidget(QLabel("Preferred location and timezone"))
        card_layout.addWidget(self.location_city_input)
        card_layout.addWidget(self.location_country_input)
        card_layout.addWidget(self.location_timezone_input)
        card_layout.addWidget(self.confirm_location_checkbox)
        card_layout.addWidget(self.save_location_button)
        card_layout.addWidget(self.delete_location_button)
        card_layout.addWidget(self.clear_weather_cache_button)
        card_layout.addWidget(self.location_status)
        card_layout.addWidget(self.preferred_timezone_setting)
        card_layout.addWidget(self.usage_memory_privacy_note)
        card_layout.addWidget(self.study_folders_setting)
        card_layout.addWidget(self.add_study_folder_button)
        card_layout.addWidget(self.remove_study_folder_button)
        card_layout.addWidget(QLabel("Frequently and recently opened items"))
        card_layout.addWidget(self.usage_memory_search_input)
        card_layout.addWidget(self.usage_memory_sort)
        card_layout.addWidget(self.usage_memory_list)
        card_layout.addWidget(self.refresh_usage_memory_button)
        card_layout.addWidget(self.delete_usage_memory_button)
        card_layout.addWidget(self.clear_usage_memory_button)
        card_layout.addWidget(self.usage_memory_status)
        card_layout.addWidget(QLabel("Explicitly saved memories"))
        card_layout.addWidget(self.saved_memories_list)
        card_layout.addWidget(self.delete_saved_memory_button)
        card_layout.addWidget(self.saved_memory_status)
        self._build_task_manager_section(card_layout)

        about_card = QFrame()
        about_card.setObjectName("aboutFlexCard")
        about_card.setStyleSheet(
            "QFrame#aboutFlexCard { background-color: #100819; "
            "border: 1px solid #71339a; border-radius: 16px; }"
        )
        about_layout = QVBoxLayout(about_card)
        about_layout.setContentsMargins(14, 12, 14, 12)
        about_layout.setSpacing(6)
        about_heading = QLabel("ABOUT FLEX")
        about_heading.setStyleSheet("font-size: 15pt; font-weight: 700;")
        about_layout.addWidget(about_heading)
        self.about_identity_label = QLabel(
            "<b>FLEX</b><br>Your Personal AI Assistant"
        )
        self.about_identity_label.setTextFormat(Qt.TextFormat.RichText)
        self.about_identity_label.setObjectName("aboutIdentity")
        about_layout.addWidget(self.about_identity_label)
        self.about_creator_label = QLabel(
            "Created &amp; Developed by<br><b>Ayman Khan</b><br>Creator &amp; Owner of FLEX"
        )
        self.about_creator_label.setTextFormat(Qt.TextFormat.RichText)
        self.about_creator_label.setObjectName("aboutCreator")
        about_layout.addWidget(self.about_creator_label)
        self.about_creator_links = QLabel(
            '<a href="https://aymanweb.vercel.app">Portfolio</a>'
            ' | <a href="https://aymanbio.vercel.app">Personal Website</a>'
            ' | <a href="https://github.com/AYMAN-KHAN-000">GitHub</a>'
        )
        self.about_creator_links.setTextFormat(Qt.TextFormat.RichText)
        self.about_creator_links.setObjectName("aboutCreatorLinks")
        self.about_creator_links.setOpenExternalLinks(True)
        self.about_creator_links.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        self.about_creator_links.setStyleSheet(
            "QLabel#aboutCreatorLinks a { color: #c783f2; font-weight: 600; }"
            "QLabel#aboutCreatorLinks a:hover { color: #ffffff; }"
        )
        about_layout.addWidget(self.about_creator_links)
        about_powered_by = QLabel("Powered by configurable AI technology.")
        about_powered_by.setObjectName("muted")
        about_layout.addWidget(about_powered_by)
        card_layout.addWidget(about_card)

        self.start_overlay_button = QPushButton("Start overlay and close main window")
        self.start_overlay_button.setObjectName("startOverlayButton")
        self.start_overlay_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.start_overlay_button.clicked.connect(self._start_overlay_mode)
        card_layout.addWidget(self.start_overlay_button)

        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        settings_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        settings_scroll.setStyleSheet("background: transparent;")
        settings_scroll.setWidget(card)
        layout.addWidget(settings_scroll, 1)

        note = QLabel(
            "FLEX will stay available from the screen edge. Reopen the main window from the system tray. "
            "OpenRouter credentials and model changes are saved in this Windows user account."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        return page

    def _build_task_manager_section(self, parent_layout: QVBoxLayout) -> None:
        section = QFrame()
        section.setObjectName("taskManagerCard")
        section.setStyleSheet(
            "QFrame#taskManagerCard { background-color: #100819; "
            "border: 1px solid #71339a; border-radius: 16px; }"
        )
        layout = QVBoxLayout(section)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        heading = QLabel("TASK MANAGER")
        heading.setStyleSheet("font-size: 15pt; font-weight: 700;")
        layout.addWidget(heading)

        self.task_notifications_checkbox = QCheckBox("Enable all task notifications")
        self.task_notifications_checkbox.setChecked(self.task_notifications_enabled)
        self.task_notifications_checkbox.toggled.connect(
            lambda value: self._set_task_notification_setting(
                "task_notifications_enabled", value
            )
        )
        self.task_start_notifications_checkbox = QCheckBox("Notify when tasks start")
        self.task_start_notifications_checkbox.setChecked(
            self.task_start_notifications_enabled
        )
        self.task_start_notifications_checkbox.toggled.connect(
            lambda value: self._set_task_notification_setting(
                "task_start_notifications_enabled", value
            )
        )
        self.task_end_notifications_checkbox = QCheckBox("Notify when tasks end")
        self.task_end_notifications_checkbox.setChecked(
            self.task_end_notifications_enabled
        )
        self.task_end_notifications_checkbox.toggled.connect(
            lambda value: self._set_task_notification_setting(
                "task_end_notifications_enabled", value
            )
        )
        self.task_notification_sound_checkbox = QCheckBox("Play notification sound")
        self.task_notification_sound_checkbox.setChecked(
            self.task_notification_sound_enabled
        )
        self.task_notification_sound_checkbox.toggled.connect(
            lambda value: self._set_task_notification_setting(
                "task_notification_sound_enabled", value
            )
        )
        self.task_auto_cleanup_checkbox = QCheckBox(
            "Automatically remove completed tasks"
        )
        self.task_auto_cleanup_checkbox.setChecked(self.task_auto_cleanup_enabled)
        self.task_auto_cleanup_checkbox.toggled.connect(
            lambda value: self._set_task_notification_setting(
                "task_auto_cleanup_enabled", value
            )
        )
        for checkbox in (
            self.task_notifications_checkbox,
            self.task_start_notifications_checkbox,
            self.task_end_notifications_checkbox,
            self.task_notification_sound_checkbox,
            self.task_auto_cleanup_checkbox,
        ):
            layout.addWidget(checkbox)

        duration_row = QHBoxLayout()
        duration_row.addWidget(QLabel("Notification duration (seconds)"))
        self.task_notification_duration = QSpinBox()
        self.task_notification_duration.setRange(3, 60)
        self.task_notification_duration.setValue(
            max(3, min(60, self.task_notification_duration_ms // 1_000))
        )
        self.task_notification_duration.valueChanged.connect(
            self._set_task_notification_duration
        )
        duration_row.addWidget(self.task_notification_duration)
        duration_row.addStretch(1)
        layout.addLayout(duration_row)

        layout.addWidget(QLabel("Upcoming and active tasks"))
        self.task_list = QListWidget()
        self.task_list.setObjectName("taskList")
        self.task_list.setMaximumHeight(150)
        self.task_list.currentItemChanged.connect(self._select_task_for_editing)
        layout.addWidget(self.task_list)

        self.task_title_input = QLineEdit()
        self.task_title_input.setObjectName("settingsInput")
        self.task_title_input.setPlaceholderText("Task name")
        layout.addWidget(self.task_title_input)
        time_row = QHBoxLayout()
        self.task_start_input = QDateTimeEdit(QDateTime.currentDateTime())
        self.task_start_input.setDisplayFormat("yyyy-MM-dd hh:mm AP")
        self.task_start_input.setCalendarPopup(True)
        self.task_start_input.setToolTip(
            f"Task local time zone: {self.task_manager.default_timezone}"
        )
        time_row.addWidget(QLabel("Start"))
        time_row.addWidget(self.task_start_input, 1)
        layout.addLayout(time_row)
        self.task_has_end_checkbox = QCheckBox("Set an end time")
        self.task_has_end_checkbox.setChecked(True)
        self.task_has_end_checkbox.toggled.connect(
            self.task_end_input_enabled
        )
        layout.addWidget(self.task_has_end_checkbox)
        end_row = QHBoxLayout()
        self.task_end_input = QDateTimeEdit(
            QDateTime.currentDateTime().addSecs(60 * 60)
        )
        self.task_end_input.setDisplayFormat("yyyy-MM-dd hh:mm AP")
        self.task_end_input.setCalendarPopup(True)
        end_row.addWidget(QLabel("End"))
        end_row.addWidget(self.task_end_input, 1)
        layout.addLayout(end_row)
        self.task_notify_start_checkbox = QCheckBox("Notify at start")
        self.task_notify_start_checkbox.setChecked(True)
        self.task_notify_end_checkbox = QCheckBox("Notify at end")
        self.task_notify_end_checkbox.setChecked(True)
        task_notify_row = QHBoxLayout()
        task_notify_row.addWidget(self.task_notify_start_checkbox)
        task_notify_row.addWidget(self.task_notify_end_checkbox)
        layout.addLayout(task_notify_row)

        edit_buttons = QHBoxLayout()
        self.create_task_button = QPushButton("Create task")
        self.create_task_button.clicked.connect(self._create_task_from_settings)
        self.save_task_button = QPushButton("Save selected task")
        self.save_task_button.clicked.connect(self._save_selected_task)
        edit_buttons.addWidget(self.create_task_button)
        edit_buttons.addWidget(self.save_task_button)
        layout.addLayout(edit_buttons)
        management_buttons = QHBoxLayout()
        self.pause_task_button = QPushButton("Pause")
        self.pause_task_button.clicked.connect(
            lambda: self._manage_selected_task("pause")
        )
        self.resume_task_button = QPushButton("Resume")
        self.resume_task_button.clicked.connect(
            lambda: self._manage_selected_task("resume")
        )
        self.cancel_task_button = QPushButton("Cancel")
        self.cancel_task_button.clicked.connect(
            lambda: self._manage_selected_task("cancel")
        )
        self.delete_task_button = QPushButton("Delete")
        self.delete_task_button.clicked.connect(
            lambda: self._manage_selected_task("delete")
        )
        for button in (
            self.pause_task_button,
            self.resume_task_button,
            self.cancel_task_button,
            self.delete_task_button,
        ):
            management_buttons.addWidget(button)
        layout.addLayout(management_buttons)
        self.task_manager_status = QLabel("")
        self.task_manager_status.setObjectName("muted")
        self.task_manager_status.setWordWrap(True)
        layout.addWidget(self.task_manager_status)
        parent_layout.addWidget(section)
        self.task_end_input.setEnabled(True)
        self._refresh_task_list()
        self._select_task_for_editing(None, None)

    def task_end_input_enabled(self, enabled: bool) -> None:
        self.task_end_input.setEnabled(enabled)
        self.task_notify_end_checkbox.setEnabled(enabled)

    def _set_task_notification_setting(self, name: str, enabled: bool) -> None:
        if name == "task_notifications_enabled":
            self.task_notifications_enabled = enabled
        elif name == "task_start_notifications_enabled":
            self.task_start_notifications_enabled = enabled
        elif name == "task_end_notifications_enabled":
            self.task_end_notifications_enabled = enabled
        elif name == "task_notification_sound_enabled":
            self.task_notification_sound_enabled = enabled
        elif name == "task_auto_cleanup_enabled":
            self.task_auto_cleanup_enabled = enabled
        else:
            raise ValueError(f"Unknown task notification setting: {name}")
        self.application_settings.setValue(name, enabled)

    def _set_task_notification_duration(self, seconds: int) -> None:
        self.task_notification_duration_ms = seconds * 1_000
        self.application_settings.setValue(
            "task_notification_duration_ms",
            self.task_notification_duration_ms,
        )

    def _task_editor_datetime(self, edit: QDateTimeEdit) -> str:
        return edit.dateTime().toString("yyyy-MM-dd'T'HH:mm:ss")

    def _create_task_from_settings(self) -> None:
        title = self.task_title_input.text().strip()
        if not title:
            self.task_manager_status.setText("Enter a task name.")
            return
        try:
            result = self.task_manager.create_task(
                title,
                self._task_editor_datetime(self.task_start_input),
                self._task_editor_datetime(self.task_end_input)
                if self.task_has_end_checkbox.isChecked()
                else None,
                notify_start=self.task_notify_start_checkbox.isChecked(),
                notify_end=(
                    self.task_notify_end_checkbox.isChecked()
                    and self.task_has_end_checkbox.isChecked()
                ),
            )
        except (TaskStorageError, ValueError) as exc:
            logger.exception("Could not create task from FLEX Settings.")
            self.task_manager_status.setText(str(exc))
            return
        warnings = result.get("warnings", [])
        self._refresh_task_list()
        self.task_manager_status.setText(
            "Task set."
            + (f" Warning: {' '.join(warnings)}" if warnings else "")
        )

    def _save_selected_task(self) -> None:
        item = self.task_list.currentItem()
        if item is None:
            self.task_manager_status.setText("Select a task to edit.")
            return
        try:
            self.task_manager.edit_task(
                str(item.data(Qt.ItemDataRole.UserRole)),
                title=self.task_title_input.text(),
                start=self._task_editor_datetime(self.task_start_input),
                end=self._task_editor_datetime(self.task_end_input)
                if self.task_has_end_checkbox.isChecked()
                else None,
                notify_start=self.task_notify_start_checkbox.isChecked(),
                notify_end=(
                    self.task_notify_end_checkbox.isChecked()
                    and self.task_has_end_checkbox.isChecked()
                ),
            )
        except (KeyError, TaskStorageError, ValueError) as exc:
            logger.exception("Could not update task from FLEX Settings.")
            self.task_manager_status.setText(str(exc))
            return
        self.task_manager_status.setText("Task updated.")
        self._refresh_task_list()

    def _manage_selected_task(self, action: str) -> None:
        item = self.task_list.currentItem()
        if item is None:
            self.task_manager_status.setText("Select a task first.")
            return
        task_id = str(item.data(Qt.ItemDataRole.UserRole))
        try:
            if action == "pause":
                self.task_manager.pause_task(task_id)
            elif action == "resume":
                self.task_manager.resume_task(task_id)
            elif action == "cancel":
                self.task_manager.cancel_task(task_id)
            elif action == "delete":
                self.task_manager.delete_task(task_id)
            else:
                raise ValueError(f"Unknown task action: {action}")
        except (KeyError, TaskStorageError, ValueError) as exc:
            logger.exception("Could not %s task from FLEX Settings.", action)
            self.task_manager_status.setText(str(exc))
            return
        action_labels = {
            "pause": "Task paused.",
            "resume": "Task resumed.",
            "cancel": "Task cancelled.",
            "delete": "Task deleted.",
        }
        self.task_manager_status.setText(action_labels[action])
        self._refresh_task_list()

    def _select_task_for_editing(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        buttons_enabled = current is not None
        for button in (
            self.save_task_button,
            self.pause_task_button,
            self.resume_task_button,
            self.cancel_task_button,
            self.delete_task_button,
        ):
            button.setEnabled(buttons_enabled)
        if current is None:
            return
        try:
            task = next(
                task for task in self.task_manager.get_tasks()
                if task["id"] == current.data(Qt.ItemDataRole.UserRole)
            )
        except (StopIteration, TaskStorageError):
            return
        from datetime import datetime
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(task["timezone"])
        start = datetime.fromisoformat(task["start"]).astimezone(zone)
        self.task_title_input.setText(task["title"])
        self.task_start_input.setDateTime(
            QDateTime.fromString(
                start.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S"),
                Qt.DateFormat.ISODate,
            )
        )
        end_value = task.get("end")
        self.task_has_end_checkbox.setChecked(end_value is not None)
        if end_value:
            end = datetime.fromisoformat(end_value).astimezone(zone)
            self.task_end_input.setDateTime(
                QDateTime.fromString(
                    end.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S"),
                    Qt.DateFormat.ISODate,
                )
            )
        self.task_notify_start_checkbox.setChecked(task["notify_start"])
        self.task_notify_end_checkbox.setChecked(task["notify_end"])

    def _refresh_task_list(self) -> None:
        if not hasattr(self, "task_list"):
            return
        try:
            tasks = self.task_manager.get_tasks()
        except TaskStorageError as exc:
            logger.exception("Could not refresh the FLEX task list.")
            if hasattr(self, "task_manager_status"):
                self.task_manager_status.setText(f"Task storage error: {exc}")
            return
        selected_id = (
            self.task_list.currentItem().data(Qt.ItemDataRole.UserRole)
            if self.task_list.currentItem() is not None
            else None
        )
        self.task_list.clear()
        from datetime import datetime
        from zoneinfo import ZoneInfo

        for task in tasks:
            zone = ZoneInfo(task["timezone"])
            start = datetime.fromisoformat(task["start"]).astimezone(zone)
            start_text = start.strftime("%b %d, %I:%M %p").replace(" 0", " ")
            if task["end"]:
                end = datetime.fromisoformat(task["end"]).astimezone(zone)
                start_text += f" – {end.strftime('%I:%M %p').lstrip('0')}"
            notifications = (
                "start/end" if task["notify_start"] and task["notify_end"]
                else "start" if task["notify_start"]
                else "end" if task["notify_end"]
                else "off"
            )
            item = QListWidgetItem(
                f"{task['title']}  |  {start_text}  |  "
                f"{task['status'].title()}  |  Notifications: {notifications}"
            )
            item.setData(Qt.ItemDataRole.UserRole, task["id"])
            self.task_list.addItem(item)
            if task["id"] == selected_id:
                self.task_list.setCurrentItem(item)
        if hasattr(self, "task_manager_status") and not self.task_manager_status.text().startswith(
            "Task storage error:"
        ):
            self.task_manager_status.setText(f"{len(tasks)} task(s) scheduled.")

    def _set_usage_tracking(self, enabled: bool) -> None:
        self.usage_tracking_enabled = enabled
        self.application_settings.setValue("usage_tracking_enabled", enabled)
        self.usage_memory_status.setText(
            "Usage tracking is on." if enabled else "Usage tracking is off."
        )

    def _set_usage_category_tracking(self, category: str, enabled: bool) -> None:
        setting_names = {
            "application": "usage_application_tracking_enabled",
            "website": "usage_website_tracking_enabled",
            "media": "usage_media_tracking_enabled",
        }
        if category not in setting_names:
            raise ValueError(f"Unsupported usage tracking category: {category}")
        if category == "application":
            self.usage_application_tracking_enabled = enabled
        elif category == "website":
            self.usage_website_tracking_enabled = enabled
        else:
            self.usage_media_tracking_enabled = enabled
        self.application_settings.setValue(setting_names[category], enabled)

    def _add_study_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Choose a study folder to index",
            str(Path.home()),
        )
        if not directory:
            return
        folder = str(Path(directory).resolve())
        if folder in self.study_folders:
            self.usage_memory_status.setText("That study folder is already listed.")
            return
        try:
            indexed = self.usage_memory.index_study_folder(folder)
        except (OSError, sqlite3.Error, ValueError) as exc:
            logger.exception("Could not index selected study folder.")
            self.usage_memory_status.setText(f"Could not add study folder: {exc}")
            return
        self.study_folders.append(folder)
        self.application_settings.setValue("study_folders", self.study_folders)
        self.study_folders_setting.setText("Study folders: " + ", ".join(self.study_folders))
        self.usage_memory_status.setText(f"Added study folder; indexed {indexed} document(s).")
        self._refresh_usage_memory_list()

    def _remove_study_folder(self) -> None:
        if not self.study_folders:
            self.usage_memory_status.setText("No study folders are configured.")
            return
        folder, accepted = QInputDialog.getItem(
            self,
            "Remove study folder",
            "Choose a folder to stop indexing:",
            self.study_folders,
            0,
            False,
        )
        if not accepted:
            return
        self.study_folders.remove(folder)
        self.application_settings.setValue("study_folders", self.study_folders)
        self.study_folders_setting.setText(
            "Study folders: " + (", ".join(self.study_folders) or "None")
        )
        self.usage_memory_status.setText(
            "Removed the folder. Previously indexed records remain until deleted or cleared."
        )

    def _refresh_usage_memory_list(self) -> None:
        self.usage_memory_list.clear()
        try:
            query = self.usage_memory_search_input.text().strip()
            if query:
                items = self.usage_memory.search(query, limit=100)
            else:
                items = self.usage_memory.list_items(
                    sort=str(self.usage_memory_sort.currentData()),
                    limit=100,
                )
        except (OSError, sqlite3.Error, ValueError):
            logger.exception("Could not load local usage memory.")
            self.usage_memory_status.setText("Could not load usage history from the local database.")
            return
        for item in items:
            opened = item["last_opened"] or "not opened yet"
            label = (
                f'{item["name"]} [{item["item_type"]}] — {item["open_count"]} opens; '
                f'last opened {opened}'
            )
            if not item["location"].startswith(("http://", "https://")) and not Path(
                item["location"]
            ).exists():
                label += " (missing or unavailable)"
            widget_item = QListWidgetItem(label)
            widget_item.setData(Qt.ItemDataRole.UserRole, item["id"])
            self.usage_memory_list.addItem(widget_item)
        if not items:
            self.usage_memory_status.setText("No usage items recorded yet.")

    def _handle_usage_memory_sort_changed(self, index: int) -> None:
        if index >= 0:
            self._refresh_usage_memory_list()

    def _delete_selected_usage_item(self) -> None:
        selected = self.usage_memory_list.currentItem()
        if selected is None:
            self.usage_memory_status.setText("Select a usage item to remove.")
            return
        item_id = selected.data(Qt.ItemDataRole.UserRole)
        answer = QMessageBox.question(
            self,
            "Remove usage item",
            f"Remove '{selected.text()}' from Personal Usage Memory?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.usage_memory.delete_item(int(item_id))
        except (KeyError, OSError, sqlite3.Error, ValueError) as exc:
            logger.exception("Could not remove usage item %s.", item_id)
            self.usage_memory_status.setText(f"Could not remove usage item: {exc}")
            return
        self.usage_memory_status.setText("Usage item removed.")
        self._refresh_usage_memory_list()

    def _clear_usage_memory(self) -> None:
        answer = QMessageBox.question(
            self,
            "Clear usage history",
            "Delete all Personal Usage Memory items? This does not remove explicitly saved memories.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            removed = self.usage_memory.clear()
        except (OSError, sqlite3.Error) as exc:
            logger.exception("Could not clear local usage history.")
            self.usage_memory_status.setText(f"Could not clear usage history: {exc}")
            return
        self.usage_memory_status.setText(f"Cleared {removed} usage item(s).")
        self._refresh_usage_memory_list()

    def _refresh_saved_memories_list(self) -> None:
        self.saved_memories_list.clear()
        try:
            memories = self.memory_database.list_memories(limit=500)
        except (OSError, sqlite3.Error, ValueError):
            logger.exception("Could not load explicitly saved memories.")
            self.saved_memory_status.setText("Could not load saved memories.")
            return
        for memory in memories:
            label = f'{memory["category"]}: {memory["key"]} — {memory["value"]}'
            widget_item = QListWidgetItem(label)
            widget_item.setData(Qt.ItemDataRole.UserRole, memory["id"])
            self.saved_memories_list.addItem(widget_item)
        if not memories:
            self.saved_memory_status.setText("No explicitly saved memories.")

    def _delete_selected_saved_memory(self) -> None:
        selected = self.saved_memories_list.currentItem()
        if selected is None:
            self.saved_memory_status.setText("Select a saved memory to remove.")
            return
        memory_id = selected.data(Qt.ItemDataRole.UserRole)
        answer = QMessageBox.question(
            self,
            "Remove saved memory",
            f"Delete '{selected.text()}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.memory_database.delete_memory(int(memory_id))
        except (KeyError, OSError, sqlite3.Error, ValueError) as exc:
            logger.exception("Could not delete saved memory %s.", memory_id)
            self.saved_memory_status.setText(f"Could not remove saved memory: {exc}")
            return
        self.saved_memory_status.setText("Saved memory removed.")
        self._refresh_saved_memories_list()

    def _register_research_tools(self) -> None:
        self.research_tool_specs = create_research_tools(
            self.research_engine,
            self.research_history,
            enabled=True,
            open_page=self._open_research_page,
        )
        for tool in self.research_tool_specs:
            self.registry.register(tool)

    def _open_research_page(
        self,
        url: str,
        browser: str | None = None,
    ) -> dict[str, object]:
        tool = self.registry.get("open_website")
        arguments = {
            "website": url,
            "browser": browser or self.settings.browser_default,
        }
        result = tool.executor(**arguments)
        if tool.verification is not None and not tool.verification(arguments, result):
            raise RuntimeError("The configured browser did not verify a successful open.")
        return result

    def _set_smart_research_enabled(self, enabled: bool) -> None:
        self.smart_research_enabled = enabled
        self.application_settings.setValue("smart_research_enabled", enabled)
        if enabled:
            if not any(name in self.registry for name in (
                "research_web",
                "open_recommended_page",
                "get_research_history",
                "clear_research_history",
            )):
                self._register_research_tools()
        else:
            for name in (
                "research_web",
                "open_recommended_page",
                "get_research_history",
                "clear_research_history",
            ):
                self.registry.unregister(name)
        self.research_history_status.setText(
            "Smart Research is enabled." if enabled else "Smart Research is disabled."
        )

    def _refresh_research_history(self) -> None:
        self.research_history_list.clear()
        try:
            records = self.research_history.list(20)
        except sqlite3.Error:
            logger.exception("Could not load local research history.")
            self.research_history_status.setText("Could not load research history.")
            return
        for record in records:
            opened = "opened" if record["opened"] else "not opened"
            title = (
                record["selected_result"]["title"]
                if record["selected_result"]
                else "No selected result"
            )
            item = QListWidgetItem(
                f'{record["created_at"]} — {record["request"]} — {title} ({opened})'
            )
            item.setData(Qt.ItemDataRole.UserRole, record["id"])
            if record["selected_result"]:
                item.setToolTip(str(record["selected_result"].get("url", "")))
            self.research_history_list.addItem(item)
        if not records:
            self.research_history_status.setText("No local research history.")

    def _clear_research_history(self) -> None:
        answer = QMessageBox.question(
            self,
            "Clear research history",
            "Delete all locally saved research requests and source URLs?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            deleted = self.research_history.clear()
        except sqlite3.Error as exc:
            logger.exception("Could not clear local research history.")
            self.research_history_status.setText(f"Could not clear research history: {exc}")
            return
        self.research_history_status.setText(f"Deleted {deleted} research record(s).")
        self._refresh_research_history()

    def _on_setting_changed(self, name: str, value: object) -> None:
        if name == "preferred_timezone":
            timezone = str(value) if value else "Asia/Dhaka"
            try:
                self.task_manager.set_default_timezone(timezone)
            except ValueError:
                logger.exception("Could not apply the preferred task timezone.")
                if hasattr(self, "task_manager_status"):
                    self.task_manager_status.setText(
                        f"Invalid preferred timezone: {timezone}"
                    )
                return
            if hasattr(self, "task_start_input"):
                self.task_start_input.setToolTip(
                    f"Task local time zone: {timezone}"
                )
        if name == "study_folder":
            self.study_folder_setting.setText(
                "Preferred study folder: " + (str(value) if value else "Not set")
            )
            return
        if name in {"preferred_location", "preferred_timezone"}:
            location = self.location_manager.get()
            self.location_city_input.setText(location.city if location else "")
            self.location_country_input.setText(location.country if location else "")
            self.location_timezone_input.setText(location.timezone if location else "")
            self.location_status.setText(
                "Saved location: "
                + (
                    f"{location.city}, {location.country} ({location.timezone})"
                    if location
                    else "Not set"
                )
            )
            self._refresh_preferred_timezone_label()

    def _refresh_preferred_timezone_label(self) -> None:
        timezone = self.settings_manager.get_setting("preferred_timezone")
        self.preferred_timezone_setting.setText(
            "Preferred timezone: "
            + (timezone or "Use the system timezone")
        )

    def _set_weather_enabled(self, enabled: bool) -> None:
        self.weather_enabled = enabled
        self.application_settings.setValue("weather_enabled", enabled)
        weather_tool = next(
            tool
            for tool in self.context_tool_specs
            if tool.name == "get_weather_forecast"
        )
        if enabled:
            self.registry.register(weather_tool)
        else:
            self.registry.unregister("get_weather_forecast")

    def _save_location_settings(self) -> None:
        if not self.confirm_location_checkbox.isChecked():
            self.location_status.setText("Confirm the location before saving it.")
            return
        try:
            location = self.location_manager.save(
                self.location_city_input.text(),
                self.location_country_input.text(),
                self.location_timezone_input.text(),
                user_confirmed=True,
            )
        except (ValueError, sqlite3.Error) as exc:
            logger.exception("Could not save preferred location.")
            self.location_status.setText(f"Could not save location: {exc}")
            return
        self.confirm_location_checkbox.setChecked(False)
        self.location_status.setText(
            f"Saved location: {location.city}, {location.country} ({location.timezone})"
        )
        self._refresh_preferred_timezone_label()

    def _delete_saved_location(self) -> None:
        answer = QMessageBox.question(
            self,
            "Delete saved location",
            "Delete the saved city, country, timezone, and cached weather data?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.settings_manager.delete_location()
        except (OSError, sqlite3.Error) as exc:
            logger.exception("Could not delete preferred location.")
            self.location_status.setText(f"Could not delete location: {exc}")
            return
        self.location_city_input.clear()
        self.location_country_input.clear()
        self.location_timezone_input.clear()
        self.location_status.setText("Saved location deleted.")

    def _clear_weather_cache(self) -> None:
        try:
            removed = self.weather_service.clear_cache()
        except sqlite3.Error as exc:
            logger.exception("Could not clear cached weather.")
            self.location_status.setText(f"Could not clear weather cache: {exc}")
            return
        self.location_status.setText(f"Cleared {removed} cached weather item(s).")

    def _save_openrouter_settings(self) -> None:
        model = self.model_input.text().strip()
        if not model:
            self.openrouter_save_status.setText("Enter an OpenRouter model name.")
            self.model_input.setFocus()
            return

        api_key = self.api_key_input.text().strip()
        self.application_settings.setValue("openrouter_model", model)
        self.application_settings.setValue("openrouter_api_key", api_key)
        self.application_settings.sync()
        if self.application_settings.status() != QSettings.Status.NoError:
            self.openrouter_save_status.setText(
                "Could not save OpenRouter settings. Check the Windows user settings store."
            )
            return

        self.settings.openrouter_model = model
        self.settings.openrouter_api_key = api_key
        self.provider.model = model
        self.provider.api_key = api_key
        self.model_input.setText(model)
        self.openrouter_save_status.setText(
            "OpenRouter settings saved."
            if api_key
            else "Model saved. No API key is configured."
        )

    def _setup_system_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        tray_icon = QSystemTrayIcon(load_application_icon(), self)
        tray_icon.setToolTip("FLEX")
        tray_menu = QMenu(self)

        show_action = QAction("Show FLEX", self)
        show_action.triggered.connect(self._show_main_window)
        tray_menu.addAction(show_action)

        start_overlay_action = QAction("Start overlay", self)
        start_overlay_action.triggered.connect(self._start_overlay_mode)
        tray_menu.addAction(start_overlay_action)
        tray_menu.addSeparator()

        exit_action = QAction("Exit FLEX", self)
        app = QApplication.instance()
        if app is not None:
            exit_action.triggered.connect(app.quit)
        tray_menu.addAction(exit_action)

        tray_icon.setContextMenu(tray_menu)
        tray_icon.activated.connect(self._handle_tray_activation)
        tray_icon.show()
        self.tray_icon = tray_icon

    def _show_main_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _handle_tray_activation(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_main_window()

    def _start_overlay_mode(self) -> None:
        self.edge_overlay.activate_overlay_mode()
        self.hide()

    def _show_page(self, index: int) -> None:
        if index != 1 and self._voice_chat_enabled:
            self._set_voice_chat_enabled(False)
        self.pages.setCurrentIndex(index)
        self.sidebar.setVisible(index != 0)
        self.composer_frame.setVisible(index == 1)
        (
            self.home_button,
            self.chat_button,
            self.discord_button,
            self.settings_button,
        )[index].setChecked(True)
        self.input_box.setEnabled(self._request_worker is None)
        self.send_button.setEnabled(self._request_worker is None)
        self.cancel_button.setVisible(self._request_worker is not None)
        self.cancel_button.setEnabled(self._request_worker is not None)

    def _update_discord_status(self, status: str) -> None:
        self.settings.discord_status = status
        if hasattr(self, "discord_status_label"):
            self.discord_status_label.setText(status)

    def _append_response(self, text: str) -> None:
        self.chat_output.append_markdown_message("FLEX", text)

    def _append_user(self, text: str) -> None:
        self.chat_output.append_plain_message("You", text)

    def _handle_overlay_submit(self, text: str) -> None:
        if not text:
            return
        self.input_box.setText(text)
        self._show_page(1)
        self._handle_submit()

    def _handle_submit(self) -> None:
        if self._request_worker is not None:
            return
        request = self.input_box.text().strip()
        if not request:
            return

        self._wake_voice_notch()
        self._voice_idle_timer.stop()
        if self._voice_chat_enabled:
            notch_command = _voice_notch_command(request)
            if notch_command is not None:
                self.input_box.clear()
                self._apply_voice_notch_command(notch_command)
                return
        command_parts = request.casefold().split(maxsplit=1)
        is_expression_test = command_parts[0] == NOTCH_EXPRESSION_TEST_COMMAND
        expression_id = (
            command_parts[1].strip()
            if is_expression_test and len(command_parts) == 2
            else command_parts[0]
        )
        if is_expression_test:
            self.input_box.clear()
            self._stop_voice_listener()
            if expression_id not in NOTCH_EXPRESSION_IDS:
                supported_ids = ", ".join(sorted(NOTCH_EXPRESSION_IDS))
                self.status_label.setText(
                    f"Use {NOTCH_EXPRESSION_TEST_COMMAND} <emotion ID>: "
                    f"{supported_ids}"
                )
                return
            self._show_timed_expression(
                expression_id,
                HAPPY_EXPRESSION_DURATION_MS,
            )
            self.status_label.setText(f"Expression preview: {expression_id}")
            return
        if expression_id in NOTCH_EXPRESSION_IDS:
            self.input_box.clear()
            self._stop_voice_listener()
            self._show_timed_expression(
                expression_id,
                HAPPY_EXPRESSION_DURATION_MS,
            )
            self.status_label.setText(f"Expression preview: {expression_id}")
            return
        reaction = _message_expression(request)
        if reaction == "love":
            self._show_love_expression()
        elif reaction == "wonder":
            self._show_wonder_expression()
        elif _is_gratitude_message(request):
            self._show_happy_expression()
        self._stop_voice_listener()
        self._append_user(request)
        self._show_page(1)
        self.input_box.clear()
        self.status_label.setText("Thinking")
        self.input_box.setEnabled(False)
        self.send_button.setEnabled(False)
        self.cancel_button.setVisible(True)
        self.cancel_button.setEnabled(True)

        worker = RequestWorker(
            self.agent,
            request,
            self._voice_chat_enabled,
        )
        worker.response_ready.connect(self._handle_response)
        worker.error_occurred.connect(self._handle_request_error)
        worker.finished.connect(self._handle_request_finished)
        self._request_worker = worker
        worker.start()

    def _cancel_request(self) -> None:
        if self._request_worker is None:
            return
        self.agent.cancel_current_request()
        self.cancel_button.setEnabled(False)
        self.status_label.setText("Stopping")

    def _request_tool_approval(self, tool_name: str, details: str) -> bool:
        prompt = ApprovalPrompt(tool_name=tool_name, details=details)
        self.approval_requested.emit(prompt)
        if not prompt.answered.wait(timeout=300):
            return False
        return prompt.approved

    def _handle_approval_request(self, prompt: ApprovalPrompt) -> None:
        action_details = json.loads(prompt.details) if prompt.details else {}
        answer = QMessageBox.question(
            self,
            "Confirm Windows action",
            (
                "FLEX is asking for permission to perform a protected action.\n\n"
                f"Action: {prompt.tool_name}\n"
                f"Details: {json.dumps(action_details, ensure_ascii=False)}\n\n"
                "Do you want to continue?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        prompt.approved = answer == QMessageBox.StandardButton.Yes
        prompt.answered.set()

    def _handle_response(self, response: str) -> None:
        self.status_label.setText("Completed")
        self._append_response(response)
        if _is_unclear_response(response):
            self._show_confused_expression()
        elif _is_gratitude_message(response):
            self._show_happy_expression()
        self._refresh_task_list()

    def _enqueue_task_notification(self, task: dict, state: str) -> bool:
        try:
            accepted = self.edge_overlay.enqueue_task_notification(
                task,
                state,
                self.task_notification_duration_ms,
            )
        except (RuntimeError, ValueError, OSError):
            logger.exception("Could not enqueue task notification.")
            return False
        if accepted and self.task_notification_sound_enabled:
            QApplication.beep()
        return accepted

    def _check_due_tasks(self) -> None:
        try:
            self.task_manager.process_due_tasks(
                self._enqueue_task_notification,
                notifications_enabled=self.task_notifications_enabled,
                start_notifications_enabled=self.task_start_notifications_enabled,
                end_notifications_enabled=self.task_end_notifications_enabled,
                auto_cleanup=self.task_auto_cleanup_enabled,
            )
        except (TaskStorageError, ValueError):
            logger.exception("Could not process scheduled FLEX tasks.")
            if hasattr(self, "task_manager_status"):
                self.task_manager_status.setText(
                    "Task scheduler error. See the FLEX log for details."
                )
        self._refresh_task_list()

    def _handle_request_error(self, error: str) -> None:
        self.status_label.setText("Failed")
        self._append_response(f"I hit an error: {error}")
        if self._voice_chat_enabled:
            self._show_main_window()
            self._show_page(1)
        QMessageBox.critical(self, "FLEX Error", error)

    def _handle_request_finished(self) -> None:
        self.input_box.setEnabled(True)
        self.send_button.setEnabled(True)
        self.cancel_button.setVisible(False)
        self.cancel_button.setEnabled(False)
        self._request_worker = None
        if self._voice_chat_enabled:
            self._start_voice_listener()

    def _toggle_voice_chat(self) -> None:
        self._set_voice_chat_enabled(not self._voice_chat_enabled)

    def _set_voice_chat_enabled(self, enabled: bool) -> None:
        if enabled == self._voice_chat_enabled:
            return

        self._voice_chat_enabled = enabled
        self.voice_button.setChecked(enabled)
        self.edge_overlay.set_voice_mode(enabled)
        if enabled:
            self._voice_notch_sleeping = False
            self.voice_button.setToolTip("Stop voice chat")
            self.voice_button.setAccessibleName("Stop voice chat")
            self._start_voice_listener()
            return

        self._voice_idle_timer.stop()
        self._happy_expression_timer.stop()
        self._voice_notch_sleeping = False
        self._stop_voice_listener()
        self._restore_notch_expression()
        self.voice_button.setToolTip("Start voice chat")
        self.voice_button.setAccessibleName("Start voice chat")
        self.status_label.setText(
            "Thinking" if self._request_worker is not None else "Idle"
        )

    def _start_voice_listener(self) -> None:
        if (
            not self._voice_chat_enabled
            or self._speech_worker is not None
        ):
            return

        self.status_label.setText("Loading local speech model...")
        worker = SpeechRecognitionWorker(self)
        worker.ready.connect(self._handle_voice_ready)
        worker.speech_activity.connect(self._handle_voice_activity)
        worker.transcript_recognized.connect(self._handle_voice_transcript)
        worker.error_occurred.connect(self._handle_voice_error)
        worker.finished.connect(self._handle_voice_listener_finished)
        self._speech_worker = worker
        worker.start()

    def _stop_voice_listener(self) -> None:
        worker = self._speech_worker
        if worker is None:
            return

        worker.stop()
        if worker.wait(5000):
            self._speech_worker = None

    def _handle_voice_ready(self) -> None:
        if not self._voice_chat_enabled:
            return
        self.status_label.setText("Listening - pause for 2 seconds to send")
        self._voice_idle_timer.start(VOICE_IDLE_TIMEOUT_MS)

    def _handle_voice_transcript(self, transcript: str) -> None:
        if (
            not self._voice_chat_enabled
            or self._request_worker is not None
        ):
            return
        notch_command = _voice_notch_command(transcript)
        if notch_command is not None:
            self._apply_voice_notch_command(notch_command)
            return
        self._handle_voice_activity()
        self.input_box.setText(transcript)
        self._handle_submit()

    def _apply_voice_notch_command(self, command: str) -> None:
        self._happy_expression_timer.stop()
        if command == "sleep":
            self._voice_notch_sleeping = True
            self._voice_idle_timer.stop()
            self._restore_notch_expression()
            self.status_label.setText(
                "Sleeping - speak or send a message to wake FLEX"
            )
            return
        self._voice_notch_sleeping = False
        self._wake_voice_notch()
        self._voice_idle_timer.start(VOICE_IDLE_TIMEOUT_MS)
        self.status_label.setText("Listening - pause for 2 seconds to send")

    def _handle_voice_activity(self) -> None:
        if not self._voice_chat_enabled:
            return
        self._wake_voice_notch()
        if self._request_worker is None:
            self._voice_idle_timer.start(VOICE_IDLE_TIMEOUT_MS)

    def _handle_notch_wake(self) -> None:
        if not self._voice_chat_enabled:
            return
        self._wake_voice_notch()
        self._voice_idle_timer.start(VOICE_IDLE_TIMEOUT_MS)
        self.status_label.setText("Listening - pause for 2 seconds to send")

    def _wake_voice_notch(self) -> None:
        self._happy_expression_timer.stop()
        self._voice_notch_sleeping = False
        self._restore_notch_expression()

    def _show_happy_expression(self) -> None:
        self._show_timed_expression("happy", HAPPY_EXPRESSION_DURATION_MS)

    def _handle_voice_idle_timeout(self) -> None:
        if self._voice_chat_enabled and self._request_worker is None:
            self._happy_expression_timer.stop()
            self._voice_notch_sleeping = True
            self._restore_notch_expression()
            self.status_label.setText("Sleeping - speak or send a message to wake FLEX")

    def _handle_voice_error(self, error: str) -> None:
        self._show_main_window()
        self._show_page(1)
        self._append_response(f"Voice chat error: {error}")
        QMessageBox.warning(
            self,
            "FLEX Voice Chat",
            error,
        )
        self._voice_chat_enabled = False
        self._voice_notch_sleeping = False
        self._voice_idle_timer.stop()
        self._happy_expression_timer.stop()
        self.voice_button.setChecked(False)
        self.edge_overlay.set_voice_mode(False)
        self._restore_notch_expression()
        self.voice_button.setToolTip("Start voice chat")
        self.voice_button.setAccessibleName("Start voice chat")
        self.status_label.setText("Failed")
        self._stop_voice_listener()

    def _handle_voice_listener_finished(self) -> None:
        if self._speech_worker is not None and not self._speech_worker.isRunning():
            self._speech_worker = None
        if self._voice_chat_enabled:
            self._start_voice_listener()
