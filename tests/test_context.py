from __future__ import annotations

from datetime import datetime, timedelta
import sqlite3
from zoneinfo import ZoneInfo

import pytest
import requests

from flex.context.context_tools import create_context_tools
from flex.context.location_manager import LocationManager
from flex.context.time_service import get_local_time
from flex.context.weather_service import WeatherService


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeWeatherSession:
    def __init__(self, timezone="Asia/Dhaka"):
        self.timezone = timezone
        self.calls = []
        local_today = datetime.now(ZoneInfo(timezone)).date()
        dates = [(local_today + timedelta(days=index)).isoformat() for index in range(16)]
        hours = [
            f"{day}T{hour:02d}:00"
            for day in dates
            for hour in range(24)
        ]
        self.forecast = {
            "timezone": timezone,
            "daily": {
                "time": dates,
                "weather_code": [3] * 16,
                "temperature_2m_max": [28.0] * 16,
                "temperature_2m_min": [20.0] * 16,
                "precipitation_probability_max": [35] * 16,
                "precipitation_sum": [1.2] * 16,
                "wind_speed_10m_max": [12.0] * 16,
            },
            "hourly": {
                "time": hours,
                "temperature_2m": [25.0] * len(hours),
                "relative_humidity_2m": [70] * len(hours),
                "precipitation_probability": [10] * len(hours),
            },
            "current": {
                "temperature_2m": 25.0,
                "relative_humidity_2m": 70,
                "precipitation": 0.0,
                "weather_code": 2,
                "wind_speed_10m": 8.0,
            },
        }

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if "geocoding" in url:
            return FakeResponse(
                {
                    "results": [
                        {
                            "name": "Dhaka",
                            "country": "Bangladesh",
                            "country_code": "BD",
                            "latitude": 23.7,
                            "longitude": 90.4,
                            "timezone": self.timezone,
                        }
                    ]
                }
            )
        return FakeResponse(self.forecast)


def test_location_requires_user_confirmation_and_can_be_replaced_or_deleted(tmp_path):
    manager = LocationManager(tmp_path / "flex.db")
    with pytest.raises(ValueError, match="explicitly confirm"):
        manager.save("Dhaka", "Bangladesh", "Asia/Dhaka", user_confirmed=False)

    saved = manager.save("Dhaka", "Bangladesh", "Asia/Dhaka", user_confirmed=True)
    assert manager.get() == saved
    assert manager.save(
        "Savar",
        "Bangladesh",
        "Asia/Dhaka",
        user_confirmed=True,
    ).city == "Savar"
    assert manager.delete() is True
    assert manager.get() is None


def test_local_time_uses_requested_timezone_from_live_clock():
    result = get_local_time("Asia/Dhaka")

    preferred_time = datetime.fromisoformat(result["preferred_time"])
    assert preferred_time.utcoffset() == datetime.now(ZoneInfo("Asia/Dhaka")).utcoffset()
    assert result["preferred_timezone"] == "Asia/Dhaka"
    assert result["system_time"]


def test_missing_timezone_database_reports_missing_dependency(monkeypatch):
    from zoneinfo import ZoneInfoNotFoundError

    import flex.context.timezones as timezones

    monkeypatch.setattr(timezones.zoneinfo, "TZPATH", ())
    monkeypatch.setattr(timezones.importlib.util, "find_spec", lambda _name: None)
    monkeypatch.setattr(
        timezones,
        "ZoneInfo",
        lambda _name: (_ for _ in ()).throw(ZoneInfoNotFoundError("Asia/Dhaka")),
    )

    with pytest.raises(ValueError, match="IANA timezone data is unavailable"):
        timezones.get_iana_timezone("Asia/Dhaka")


def test_weather_fetches_fresh_forecast_then_uses_short_cache_and_can_clear(tmp_path):
    session = FakeWeatherSession()
    service = WeatherService(tmp_path / "flex.db", session=session)
    location = LocationManager(tmp_path / "flex.db").save(
        "Dhaka",
        "Bangladesh",
        "Asia/Dhaka",
        user_confirmed=True,
    )

    first = service.forecast(
        location,
        requested_date="tomorrow",
        requested_time="3 PM",
    )
    second = service.forecast(
        location,
        requested_date="tomorrow",
        requested_time="4 PM",
    )

    assert first["success"] is True
    assert first["forecast_not_observed"] is True
    assert first["forecast"]["temperature_max_c"] == 28
    assert len(first["hourly"]) == 24
    assert first["requested_hour"]["time"].endswith("T15:00")
    assert second["cached"] is True
    assert second["requested_hour"]["time"].endswith("T16:00")
    assert len(session.calls) == 2
    assert service.clear_cache() == 1


def test_weather_tool_opens_windows_weather_app_after_lookup_failure(tmp_path):
    class FailedSession:
        def get(self, *_args, **_kwargs):
            raise requests.ConnectionError("offline")

    database = tmp_path / "flex.db"
    locations = LocationManager(database)
    locations.save("Savar", "Bangladesh", "Asia/Dhaka", user_confirmed=True)
    weather = WeatherService(database, session=FailedSession())
    opened = []
    tools = {
        tool.name: tool
        for tool in create_context_tools(
            locations,
            weather,
            open_weather_app=lambda: opened.append("Weather") or {
                "status": "opened",
                "application": "Weather",
            },
        )
    }

    result = tools["get_weather_forecast"].executor(
        requested_date="tomorrow",
        requested_time="3 PM",
    )

    assert result["success"] is True
    assert result["forecast_available"] is False
    assert result["fallback"] == "opened_windows_weather_app"
    assert result["app"]["status"] == "opened"
    assert opened == ["Weather"]


def test_invalid_weather_time_does_not_open_windows_weather_app(tmp_path):
    database = tmp_path / "flex.db"
    locations = LocationManager(database)
    locations.save("Savar", "Bangladesh", "Asia/Dhaka", user_confirmed=True)
    weather = WeatherService(database, session=FakeWeatherSession())
    opened = []
    tools = {
        tool.name: tool
        for tool in create_context_tools(
            locations,
            weather,
            open_weather_app=lambda: opened.append("Weather") or {"status": "opened"},
        )
    }

    result = tools["get_weather_forecast"].executor(requested_time="not a time")

    assert result["success"] is False
    assert "Use a time such as" in result["error"]["message"]
    assert opened == []


def test_weather_limits_dates_and_requires_location(tmp_path):
    service = WeatherService(tmp_path / "flex.db", session=FakeWeatherSession())

    with pytest.raises(ValueError, match="confirmed saved location"):
        service.forecast(None)
    location = LocationManager(tmp_path / "flex.db").save(
        "Dhaka",
        "Bangladesh",
        "Asia/Dhaka",
        user_confirmed=True,
    )
    with pytest.raises(ValueError, match="only from today"):
        service.forecast(location, requested_date="2099-01-01")


def test_weather_resolves_weekdays_and_relative_day_phrases(tmp_path):
    timezone = "Asia/Dhaka"
    session = FakeWeatherSession(timezone)
    service = WeatherService(tmp_path / "flex.db", session=session)
    location = LocationManager(tmp_path / "flex.db").save(
        "Dhaka",
        "Bangladesh",
        timezone,
        user_confirmed=True,
    )
    local_today = datetime.now(ZoneInfo(timezone)).date()
    days_until_monday = (0 - local_today.weekday()) % 7 or 7

    next_monday = service.forecast(location, requested_date="next Monday")
    two_days = service.forecast(location, requested_date="in two days")

    assert next_monday["date"] == (local_today + timedelta(days=days_until_monday)).isoformat()
    assert two_days["date"] == (local_today + timedelta(days=2)).isoformat()


def test_stale_weather_cache_is_refetched(tmp_path):
    db_path = tmp_path / "flex.db"
    session = FakeWeatherSession()
    service = WeatherService(db_path, session=session)
    location = LocationManager(db_path).save(
        "Dhaka",
        "Bangladesh",
        "Asia/Dhaka",
        user_confirmed=True,
    )
    service.forecast(location, requested_date="today")
    stale_time = (datetime.now().astimezone() - timedelta(minutes=11)).isoformat()
    with sqlite3.connect(db_path) as connection:
        connection.execute("UPDATE weather_cache SET fetched_at = ?", (stale_time,))

    refreshed = service.forecast(location, requested_date="today")

    assert refreshed["cached"] is False
    assert len(session.calls) == 4


def test_weather_provider_failure_is_reported_not_cached(tmp_path):
    class FailedSession:
        def get(self, *_args, **_kwargs):
            raise requests.ConnectionError("offline")

    manager = LocationManager(tmp_path / "flex.db")
    manager.save("Dhaka", "Bangladesh", "Asia/Dhaka", user_confirmed=True)
    weather = WeatherService(tmp_path / "flex.db", session=FailedSession())
    tools = {
        tool.name: tool
        for tool in create_context_tools(manager, weather)
    }

    result = tools["get_weather_forecast"].executor(requested_date="today")

    assert result["success"] is False
    assert result["error"]["code"] == "WEATHER_LOOKUP_FAILED"
    assert weather.clear_cache() == 0


def test_context_tools_require_confirmed_location_and_respect_weather_toggle(tmp_path):
    database = tmp_path / "flex.db"
    locations = LocationManager(database)
    weather = WeatherService(database, session=FakeWeatherSession())
    tools = {
        tool.name: tool
        for tool in create_context_tools(locations, weather, weather_enabled=False)
    }

    assert "get_weather_forecast" not in tools
    rejected = tools["update_saved_location"].executor(
        city="Dhaka",
        country="Bangladesh",
        timezone="Asia/Dhaka",
        user_confirmed=False,
    )
    assert rejected["success"] is False
    saved = tools["update_saved_location"].executor(
        city="Dhaka",
        country="Bangladesh",
        timezone="Asia/Dhaka",
        user_confirmed=True,
    )
    assert saved["success"] is True
    current_time = tools["get_local_time"].executor()
    assert current_time["success"] is True
    assert current_time["preferred_timezone"] == "Asia/Dhaka"
