from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
import requests

from flex.context.location_manager import LocationManager
from flex.context.time_service import get_local_time
from flex.context.weather_service import (
    AmbiguousWeatherLocation,
    InvalidWeatherRequest,
    WeatherService,
)
from flex.security.permissions import PermissionLevel
from flex.settings.settings_manager import SettingsManager
from flex.tools.registry import ToolSpec


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UpdateLocationArgs(StrictArgs):
    city: str = Field(min_length=1, max_length=120)
    country: str | None = Field(default=None, max_length=120)
    timezone: str | None = Field(default=None, max_length=100)
    user_confirmed: bool = False


class LocalTimeArgs(StrictArgs):
    timezone: str | None = Field(default=None, max_length=100)


class WeatherArgs(StrictArgs):
    requested_date: str = Field(default="today", max_length=100)
    requested_time: str | None = Field(default=None, max_length=40)
    city: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=120)


def _tool(
    name: str,
    description: str,
    args_model: type[BaseModel],
    executor: Any,
    permission: PermissionLevel = PermissionLevel.READ_ONLY,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        schema=args_model.model_json_schema(),
        argument_model=args_model,
        executor=executor,
        permission_level=permission,
        verification=lambda _arguments, result: result.get("success") is True,
        timeout=30.0,
    )


def create_context_tools(
    locations: LocationManager,
    weather: WeatherService,
    *,
    weather_enabled: bool = True,
    settings_manager: SettingsManager | None = None,
    open_weather_app: Callable[[], dict[str, Any]] | None = None,
) -> list[ToolSpec]:
    def get_location() -> dict[str, Any]:
        try:
            location = locations.get()
        except sqlite3.Error as exc:
            return {"success": False, "error": {"code": "LOCATION_DATABASE_ERROR", "message": str(exc)}}
        return {
            "success": True,
            "location": (
                {
                    "city": location.city,
                    "country": location.country,
                    "timezone": location.timezone,
                    "confirmed_by_user": True,
                }
                if location
                else None
            ),
        }

    def update_location(
        city: str,
        country: str | None = None,
        timezone: str | None = None,
        user_confirmed: bool = False,
    ) -> dict[str, Any]:
        try:
            if settings_manager is not None:
                location = settings_manager.save_location(
                    city,
                    country,
                    user_confirmed=user_confirmed,
                    timezone=timezone,
                )
            else:
                if not user_confirmed:
                    raise ValueError(
                        "The user must explicitly ask FLEX to save the location."
                    )
                resolved = weather.resolve_location(city, country)
                if timezone and timezone != resolved.timezone:
                    raise ValueError(
                        f"The resolved timezone is {resolved.timezone}; update it separately if needed."
                    )
                location = locations.save(
                    resolved.city,
                    resolved.country,
                    resolved.timezone,
                    user_confirmed=True,
                )
                if locations.get() != location:
                    raise OSError("FLEX could not verify the saved preferred location.")
                weather.clear_cache()
        except (ValueError, OSError, sqlite3.Error, requests.RequestException) as exc:
            return {"success": False, "error": {"code": "LOCATION_UPDATE_FAILED", "message": str(exc)}}
        return {
            "success": True,
            "location": {
                "city": location.city,
                "country": location.country,
                "timezone": location.timezone,
                "confirmed_by_user": True,
            },
        }

    def delete_location() -> dict[str, Any]:
        try:
            deleted = (
                settings_manager.delete_location()
                if settings_manager is not None
                else locations.delete()
            )
            if settings_manager is None:
                weather.clear_cache()
        except (OSError, sqlite3.Error) as exc:
            return {"success": False, "error": {"code": "LOCATION_DELETE_FAILED", "message": str(exc)}}
        return {"success": True, "deleted": deleted}

    def local_time(timezone: str | None = None) -> dict[str, Any]:
        try:
            location = locations.get()
            selected_timezone = timezone
            if selected_timezone is None and settings_manager is not None:
                selected_timezone = settings_manager.get_setting("preferred_timezone")
            if selected_timezone is None and location is not None:
                selected_timezone = location.timezone
            return {"success": True, **get_local_time(selected_timezone)}
        except (ValueError, sqlite3.Error) as exc:
            return {"success": False, "error": {"code": "TIME_LOOKUP_FAILED", "message": str(exc)}}

    def open_weather_fallback(exc: Exception) -> dict[str, Any]:
        if open_weather_app is None:
            return {
                "success": False,
                "error": {"code": "WEATHER_LOOKUP_FAILED", "message": str(exc)},
            }
        try:
            opened = open_weather_app()
        except (OSError, RuntimeError) as app_exc:
            return {
                "success": False,
                "error": {
                    "code": "WEATHER_LOOKUP_AND_APP_FAILED",
                    "message": (
                        f"Weather lookup failed: {exc}. "
                        f"Could not open the Windows Weather app: {app_exc}"
                    ),
                },
            }
        return {
            "success": True,
            "forecast_available": False,
            "fallback": "opened_windows_weather_app",
            "weather_error": str(exc),
            "app": opened,
            "message": (
                "FLEX could not retrieve the forecast automatically. "
                "The Windows Weather app was opened for you to check it."
            ),
        }

    def weather_forecast(
        requested_date: str = "today",
        requested_time: str | None = None,
        city: str | None = None,
        country: str | None = None,
    ) -> dict[str, Any]:
        if not weather_enabled:
            return {"success": False, "error": {"code": "WEATHER_DISABLED", "message": "Weather lookup is disabled in Settings."}}
        try:
            result = weather.forecast(
                locations.get(),
                requested_date=requested_date,
                requested_time=requested_time,
                city=city,
                country=country,
            )
            return result
        except (InvalidWeatherRequest, AmbiguousWeatherLocation) as exc:
            return {
                "success": False,
                "error": {"code": "WEATHER_LOOKUP_FAILED", "message": str(exc)},
            }
        except (ValueError, sqlite3.Error, requests.RequestException, OSError) as exc:
            return open_weather_fallback(exc)

    tools = [
        _tool("get_saved_location", "Read the locally saved, user-confirmed city, country, and timezone.", StrictArgs, get_location),
        _tool(
            "update_saved_location",
            "Resolve and save a preferred city and optional country only when the user explicitly asks FLEX to save it. FLEX obtains and verifies the location timezone; never guess one.",
            UpdateLocationArgs,
            update_location,
            PermissionLevel.LOW_RISK,
        ),
        _tool("delete_saved_location", "Delete the saved city, country, timezone, and cached weather data after confirmation.", StrictArgs, delete_location, PermissionLevel.HIGH_RISK),
        _tool("get_local_time", "Get the current system clock and optionally the saved or requested IANA timezone. Never use memory for current time.", LocalTimeArgs, local_time),
    ]
    if weather_enabled:
        tools.append(
            _tool(
                "get_weather_forecast",
                "Fetch the requested-date/hour forecast from Open-Meteo for the saved location or a transient city. If the lookup fails, open the Windows Weather app and clearly say FLEX could not read its forecast automatically. Use requested_time for questions about a specific local time; hourly results are matched to the nearest available forecast hour. Never invent weather data or claim a forecast was observed.",
                WeatherArgs,
                weather_forecast,
                PermissionLevel.LOW_RISK,
            )
        )
    return tools
