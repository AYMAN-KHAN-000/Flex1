from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QSettings

from flex.context.location_manager import LocationManager, SavedLocation
from flex.context.weather_service import WeatherService
from flex.settings.settings_registry import SETTING_REGISTRY
from flex.settings.settings_validator import validate_study_folder, validate_timezone

SettingsFactory = Callable[[], QSettings]
SettingsChanged = Callable[[str, Any], None]


class SettingsManager:
    def __init__(
        self,
        settings_factory: SettingsFactory,
        locations: LocationManager,
        weather: WeatherService,
        search_locations: list[str],
        configured_search_locations: list[str],
        on_change: SettingsChanged | None = None,
    ) -> None:
        self._settings_factory = settings_factory
        self.locations = locations
        self.weather = weather
        self.search_locations = search_locations
        self.configured_search_locations = configured_search_locations
        self.on_change = on_change
        saved_study_folder = self._settings_factory().value("study_folder", "", type=str)
        if saved_study_folder:
            self._add_search_location(saved_study_folder)

    def list_supported_settings(self) -> list[dict[str, Any]]:
        return [
            {
                "name": definition.name,
                "description": definition.description,
                "update_tool": definition.update_tool,
                "resettable": definition.resettable,
            }
            for definition in SETTING_REGISTRY.values()
        ]

    def get_setting(self, name: str) -> Any:
        if name not in SETTING_REGISTRY:
            raise ValueError(f"Unsupported setting: {name}")
        if name == "study_folder":
            return self._settings_factory().value("study_folder", "", type=str) or None
        if name == "preferred_timezone":
            saved_timezone = self._settings_factory().value(
                "preferred_timezone",
                "",
                type=str,
            )
            if saved_timezone:
                return saved_timezone
        location = self.locations.get()
        if location is None:
            return None
        if name in {"preferred_location", "weather_location"}:
            return {
                "city": location.city,
                "country": location.country,
                "timezone": location.timezone,
            }
        if name == "preferred_timezone":
            return location.timezone
        raise ValueError(f"Unsupported setting: {name}")

    def update_setting(self, name: str, value: str) -> Any:
        if name == "study_folder":
            return self.update_study_folder(value)
        if name == "preferred_timezone":
            return self.update_preferred_timezone(value)
        if name in {"preferred_location", "weather_location"}:
            raise ValueError(
                "Use update_saved_location with a city and country to resolve this setting."
            )
        raise ValueError(f"Unsupported setting: {name}")

    def update_study_folder(self, value: str) -> str:
        folder = validate_study_folder(value)
        settings = self._settings_factory()
        previous = settings.value("study_folder", "", type=str)
        settings.setValue("study_folder", folder)
        settings.sync()
        if settings.status() != QSettings.Status.NoError:
            raise OSError("FLEX could not save the study-folder setting.")

        saved = self._settings_factory().value("study_folder", "", type=str)
        if os.path.normcase(os.path.abspath(saved)) != os.path.normcase(os.path.abspath(folder)):
            raise OSError("FLEX could not verify the saved study-folder setting.")
        self._replace_search_location(previous, saved)
        self._notify("study_folder", saved)
        return saved

    def reset_setting(self, name: str) -> None:
        if name == "preferred_timezone":
            settings = self._settings_factory()
            settings.remove("preferred_timezone")
            settings.sync()
            if settings.status() != QSettings.Status.NoError:
                raise OSError("FLEX could not remove the preferred-timezone setting.")
            if self._settings_factory().contains("preferred_timezone"):
                raise OSError("FLEX could not verify the preferred timezone was removed.")
            self._notify("preferred_timezone", self.get_setting(name))
            return
        if name != "study_folder":
            raise ValueError(f"Reset is not supported for setting: {name}")
        settings = self._settings_factory()
        previous = settings.value("study_folder", "", type=str)
        settings.remove("study_folder")
        settings.sync()
        if settings.status() != QSettings.Status.NoError:
            raise OSError("FLEX could not remove the study-folder setting.")
        if self._settings_factory().contains("study_folder"):
            raise OSError("FLEX could not verify the study-folder setting was removed.")
        self._remove_search_location(previous)
        self._notify("study_folder", None)

    def save_location(
        self,
        city: str,
        country: str | None,
        *,
        user_confirmed: bool,
        timezone: str | None = None,
    ) -> SavedLocation:
        if not user_confirmed:
            raise ValueError("The user must explicitly ask FLEX to save the location.")
        resolved = self.weather.resolve_location(city, country)
        if timezone and timezone != resolved.timezone:
            raise ValueError(
                f"The resolved timezone is {resolved.timezone}; update it separately if needed."
            )
        saved = self.locations.save(
            resolved.city,
            resolved.country,
            resolved.timezone,
            user_confirmed=True,
        )
        if self.locations.get() != saved:
            raise OSError("FLEX could not verify the saved preferred location.")
        self.weather.clear_cache()
        self._notify("preferred_location", saved)
        return saved

    def delete_location(self) -> bool:
        deleted = self.locations.delete()
        self.weather.clear_cache()
        if self.locations.get() is not None:
            raise OSError("FLEX could not verify the preferred location was removed.")
        self._notify("preferred_location", None)
        return deleted

    def update_preferred_timezone(self, value: str) -> str:
        timezone = validate_timezone(value)
        settings = self._settings_factory()
        settings.setValue("preferred_timezone", timezone)
        settings.sync()
        if settings.status() != QSettings.Status.NoError:
            raise OSError("FLEX could not save the preferred timezone.")
        saved = self._settings_factory().value(
            "preferred_timezone",
            "",
            type=str,
        )
        if saved != timezone:
            raise OSError("FLEX could not verify the saved timezone.")
        self._notify("preferred_timezone", saved)
        return saved

    def _replace_search_location(self, previous: str, current: str) -> None:
        self._remove_search_location(previous)
        self._add_search_location(current)

    def _add_search_location(self, path: str) -> None:
        if not any(
            os.path.normcase(os.path.abspath(existing))
            == os.path.normcase(os.path.abspath(path))
            for existing in self.search_locations
        ):
            self.search_locations.append(path)

    def _remove_search_location(self, path: str) -> None:
        configured = {
            os.path.normcase(os.path.abspath(existing))
            for existing in self.configured_search_locations
        }
        if not path or os.path.normcase(os.path.abspath(path)) in configured:
            return
        normalized = os.path.normcase(os.path.abspath(path))
        self.search_locations[:] = [
            existing
            for existing in self.search_locations
            if os.path.normcase(os.path.abspath(existing)) != normalized
        ]

    def _notify(self, name: str, value: Any) -> None:
        if self.on_change is not None:
            self.on_change(name, value)
