from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SettingDefinition:
    name: str
    description: str
    update_tool: str | None
    resettable: bool = False


SETTING_REGISTRY = {
    definition.name: definition
    for definition in (
        SettingDefinition(
            "study_folder",
            "Preferred folder for study-material searches.",
            "update_setting",
            resettable=True,
        ),
        SettingDefinition(
            "preferred_location",
            "Saved city and country used for local time and weather.",
            "update_saved_location",
            resettable=True,
        ),
        SettingDefinition(
            "preferred_timezone",
            "IANA timezone used for local-time answers; does not change Windows.",
            "update_setting",
            resettable=True,
        ),
        SettingDefinition(
            "weather_location",
            "Location used by weather forecasts; currently the saved preferred location.",
            "update_saved_location",
            resettable=True,
        ),
    )
}
