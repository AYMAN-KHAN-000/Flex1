from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from flex.config import load_settings
from flex.logging_config import configure_logging
from flex.single_instance import WindowsSingleInstance
from flex.ui.app_icon import load_application_icon, set_windows_app_user_model_id
from flex.ui.main_window import FlexMainWindow


def main() -> int:
    instance = WindowsSingleInstance()
    if not instance.acquire():
        return 0

    frozen = getattr(sys, "frozen", False)
    try:
        project_root = (
            Path(sys.executable).resolve().parent
            if frozen
            else Path(__file__).resolve().parent
        )
        settings = load_settings(project_root)
        if frozen:
            if "FLEX_BROWSER_DEFAULT" not in os.environ:
                settings.browser_default = "chromium"
            local_app_data = os.environ.get("LOCALAPPDATA")
            app_data_dir = (
                Path(local_app_data) / "FLEX"
                if local_app_data
                else Path.home() / "AppData" / "Local" / "FLEX"
            )
            if "FLEX_MEMORY_DB" not in os.environ:
                settings.memory_db_path = str(app_data_dir / "data" / "flex.db")
            configure_logging(app_data_dir)
        else:
            configure_logging(project_root)
        set_windows_app_user_model_id()

        app = QApplication(sys.argv)
        app.setApplicationName("FLEX")
        app.setWindowIcon(load_application_icon())
        app.setQuitOnLastWindowClosed(False)

        window = FlexMainWindow(settings=settings)
        window.show()
        return app.exec()
    finally:
        instance.close()


if __name__ == "__main__":
    raise SystemExit(main())
