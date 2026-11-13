from __future__ import annotations

from datetime import datetime

from flex.context.timezones import get_iana_timezone


def get_local_time(timezone_name: str | None = None) -> dict[str, str | bool]:
    system_now = datetime.now().astimezone()
    system_timezone = str(system_now.tzinfo)
    if not timezone_name:
        return {
            "system_time": system_now.isoformat(timespec="seconds"),
            "system_timezone": system_timezone,
            "preferred_time": system_now.isoformat(timespec="seconds"),
            "preferred_timezone": system_timezone,
            "timezone_differs": False,
        }
    preferred = datetime.now(get_iana_timezone(timezone_name))
    return {
        "system_time": system_now.isoformat(timespec="seconds"),
        "system_timezone": system_timezone,
        "preferred_time": preferred.isoformat(timespec="seconds"),
        "preferred_timezone": timezone_name,
        "timezone_differs": system_now.utcoffset() != preferred.utcoffset(),
    }
