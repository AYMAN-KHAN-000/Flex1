from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath

from flex.context.timezones import get_iana_timezone


def validate_study_folder(value: str) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 32_767:
        raise ValueError("Provide a valid absolute Windows folder path.")
    if not PureWindowsPath(cleaned).is_absolute():
        raise ValueError("Study folder must be an absolute Windows path.")

    try:
        folder = Path(cleaned).expanduser().resolve(strict=True)
        if not folder.is_dir():
            raise ValueError("The study folder path is not a directory.")
        if not os.access(folder, os.R_OK):
            raise ValueError("The study folder is not accessible.")
        with os.scandir(folder):
            pass
    except (OSError, RuntimeError) as exc:
        raise ValueError("The study folder does not exist or is not accessible.") from exc
    return str(folder)


def validate_timezone(value: str) -> str:
    timezone = value.strip()
    if not timezone or len(timezone) > 100:
        raise ValueError("Provide a valid IANA timezone such as Asia/Dhaka.")
    get_iana_timezone(timezone)
    return timezone
