from __future__ import annotations

import importlib.util
import zoneinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def get_iana_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        if not zoneinfo.TZPATH and importlib.util.find_spec("tzdata") is None:
            raise ValueError(
                "IANA timezone data is unavailable. Install FLEX's dependencies with "
                "'python -m pip install -r requirements.txt' and restart FLEX."
            ) from exc
        raise ValueError(f"Unknown IANA timezone: {name}") from exc
