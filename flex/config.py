from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Settings:
    project_root: Path
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-4o-mini"
    theme: str = "dark"
    require_confirmation: bool = True
    log_level: str = "INFO"
    trusted_apps: list[str] = field(
        default_factory=lambda: ["notepad", "chrome", "code", "powershell", "explorer"]
    )
    memory_db_path: str = "data/flex.db"
    browser_enabled: bool = True
    browser_default: str = "chrome"
    browser_timeout_ms: int = 10_000
    browser_max_tabs: int = 5
    terminal_enabled: bool = True
    terminal_timeout_ms: int = 30_000
    terminal_max_output: int = 20_000
    memory_enabled: bool = True
    memory_confirmation: bool = True
    automation_enabled: bool = True
    automation_max_steps: int = 20
    automation_max_runtime_seconds: int = 300
    automation_max_retries: int = 2
    default_tool_timeout_ms: int = 30_000
    clipboard_access_enabled: bool = False
    screenshot_directory: str = ""
    screenshot_all_monitors: bool = False
    discord_bot_token: str = ""
    discord_activation_password_hash: str = ""
    discord_prefix: str = "-flex"
    discord_connected: bool = False
    discord_status: str = "Disconnected"
    file_search_locations: list[str] = field(default_factory=list)
    brightness_minimum: int = 10
    brightness_low: int = 25
    brightness_medium: int = 50
    brightness_high: int = 75
    brightness_maximum: int = 100
    edge_position: str = "top"
    edge_line_color: str = "#a6a6a6"
    edge_line_length: int = 210
    edge_line_thickness: int = 6
    edge_line_opacity: float = 0.9
    hover_delay_ms: int = 180
    hide_delay_ms: int = 500
    bar_width: int = 420
    bar_height: int = 72
    always_on_top: bool = True
    show_over_fullscreen: bool = False
    animation_duration_ms: int = 220

    @classmethod
    def load(cls, project_root: Path | None = None) -> "Settings":
        project_root = project_root or Path(__file__).resolve().parent.parent
        load_dotenv(project_root / ".env")

        edge_position = os.getenv("FLEX_EDGE_POSITION", "top").strip().lower()
        if edge_position not in {"top", "bottom", "left", "right"}:
            edge_position = "top"
        browser_default = os.getenv("FLEX_BROWSER_DEFAULT", "chrome").strip().lower()
        if browser_default not in {"chrome", "edge", "chromium"}:
            browser_default = "chrome"

        def _as_int(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, str(default)))
            except (TypeError, ValueError):
                return default

        def _as_float(name: str, default: float) -> float:
            try:
                value = float(os.getenv(name, str(default)))
                return max(0.0, min(1.0, value))
            except (TypeError, ValueError):
                return default

        def _as_percentage(name: str, default: int) -> int:
            try:
                return max(0, min(100, int(os.getenv(name, str(default)))))
            except (TypeError, ValueError):
                return default

        def _as_bounded_int(
            name: str,
            default: int,
            minimum: int,
            maximum: int,
        ) -> int:
            try:
                value = int(os.getenv(name, str(default)))
            except (TypeError, ValueError):
                return default
            return max(minimum, min(maximum, value))

        return cls(
            project_root=project_root,
            openrouter_api_key=os.getenv("OPENROUTER_API_KEY", ""),
            openrouter_model=os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
            theme=os.getenv("FLEX_THEME", "dark"),
            require_confirmation=os.getenv("REQUIRE_CONFIRMATION", "true").lower() == "true",
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            trusted_apps=[app.strip() for app in os.getenv("TRUSTED_APPS", "notepad,chrome,code,powershell,explorer").split(",") if app.strip()],
            memory_db_path=os.getenv("FLEX_MEMORY_DB", "data/flex.db"),
            browser_enabled=os.getenv("FLEX_BROWSER_ENABLED", "true").lower() == "true",
            browser_default=browser_default,
            browser_timeout_ms=_as_bounded_int(
                "FLEX_BROWSER_TIMEOUT_MS", 10_000, 1_000, 120_000
            ),
            browser_max_tabs=_as_bounded_int("FLEX_BROWSER_MAX_TABS", 5, 1, 20),
            terminal_enabled=os.getenv("FLEX_TERMINAL_ENABLED", "true").lower() == "true",
            terminal_timeout_ms=_as_bounded_int(
                "FLEX_TERMINAL_TIMEOUT_MS", 30_000, 1_000, 300_000
            ),
            terminal_max_output=_as_bounded_int(
                "FLEX_TERMINAL_MAX_OUTPUT", 20_000, 1_024, 1_000_000
            ),
            memory_enabled=os.getenv("FLEX_MEMORY_ENABLED", "true").lower() == "true",
            memory_confirmation=os.getenv("FLEX_MEMORY_CONFIRMATION", "true").lower() == "true",
            automation_enabled=os.getenv("FLEX_AUTOMATION_ENABLED", "true").lower() == "true",
            automation_max_steps=_as_bounded_int(
                "FLEX_AUTOMATION_MAX_STEPS", 20, 1, 100
            ),
            automation_max_runtime_seconds=_as_bounded_int(
                "FLEX_AUTOMATION_MAX_RUNTIME_SECONDS", 300, 1, 3_600
            ),
            automation_max_retries=_as_bounded_int(
                "FLEX_AUTOMATION_MAX_RETRIES", 2, 0, 5
            ),
            default_tool_timeout_ms=_as_bounded_int(
                "FLEX_DEFAULT_TOOL_TIMEOUT_MS", 30_000, 1_000, 300_000
            ),
            clipboard_access_enabled=os.getenv("FLEX_CLIPBOARD_ACCESS", "false").lower() == "true",
            screenshot_directory=os.getenv("FLEX_SCREENSHOT_DIRECTORY", "").strip(),
            screenshot_all_monitors=os.getenv("FLEX_SCREENSHOT_ALL_MONITORS", "false").lower() == "true",
            discord_bot_token=os.getenv("FLEX_DISCORD_BOT_TOKEN", "").strip(),
            discord_activation_password_hash=os.getenv("FLEX_DISCORD_PASSWORD_HASH", "").strip(),
            discord_prefix=os.getenv("FLEX_DISCORD_PREFIX", "-flex").strip() or "-flex",
            discord_connected=os.getenv("FLEX_DISCORD_CONNECTED", "false").lower() == "true",
            discord_status=os.getenv("FLEX_DISCORD_STATUS", "Disconnected").strip() or "Disconnected",
            brightness_minimum=_as_percentage("FLEX_BRIGHTNESS_MINIMUM", 10),
            brightness_low=_as_percentage("FLEX_BRIGHTNESS_LOW", 25),
            brightness_medium=_as_percentage("FLEX_BRIGHTNESS_MEDIUM", 50),
            brightness_high=_as_percentage("FLEX_BRIGHTNESS_HIGH", 75),
            brightness_maximum=_as_percentage("FLEX_BRIGHTNESS_MAXIMUM", 100),
            edge_position=edge_position,
            edge_line_color=os.getenv("FLEX_EDGE_LINE_COLOR", "#a6a6a6"),
            edge_line_length=_as_int("FLEX_EDGE_LINE_LENGTH", 210),
            edge_line_thickness=_as_int("FLEX_EDGE_LINE_THICKNESS", 6),
            edge_line_opacity=_as_float("FLEX_EDGE_LINE_OPACITY", 0.9),
            hover_delay_ms=_as_int("FLEX_HOVER_DELAY_MS", 180),
            hide_delay_ms=_as_int("FLEX_HIDE_DELAY_MS", 500),
            bar_width=_as_int("FLEX_BAR_WIDTH", 420),
            bar_height=_as_int("FLEX_BAR_HEIGHT", 72),
            always_on_top=os.getenv("FLEX_ALWAYS_ON_TOP", "true").lower() == "true",
            show_over_fullscreen=os.getenv("FLEX_SHOW_OVER_FULLSCREEN", "false").lower() == "true",
            animation_duration_ms=_as_int("FLEX_ANIMATION_DURATION_MS", 220),
        )


def load_settings(project_root: Path | None = None) -> Settings:
    return Settings.load(project_root)
