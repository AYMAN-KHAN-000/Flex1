from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PySide6.QtGui import QIcon


_ICON_PATH = Path(__file__).with_name("assets") / "flex_icon.png"
_WINDOWS_APP_USER_MODEL_ID = "com.flexpc.FLEX"


def set_windows_app_user_model_id() -> None:
    if sys.platform != "win32":
        return

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    set_app_id = shell32.SetCurrentProcessExplicitAppUserModelID
    set_app_id.argtypes = (ctypes.c_wchar_p,)
    set_app_id.restype = ctypes.c_long
    result = set_app_id(_WINDOWS_APP_USER_MODEL_ID)
    if result < 0:
        raise OSError(
            result,
            "Could not set FLEX's Windows taskbar application ID.",
        )


def load_application_icon() -> QIcon:
    icon = QIcon(str(_ICON_PATH))
    if icon.isNull():
        raise FileNotFoundError(f"Could not load FLEX application icon: {_ICON_PATH}")
    return icon
