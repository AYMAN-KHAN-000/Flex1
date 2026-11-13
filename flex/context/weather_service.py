from __future__ import annotations

import json
import hashlib
import re
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import requests

from flex.context.location_manager import SavedLocation
from flex.context.timezones import get_iana_timezone

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_CACHE_MAX_AGE = timedelta(minutes=10)
_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_KNOWN_LOCATION_TIMEZONES = {
    ("savar", "bangladesh"): ("Savar", "Bangladesh", "Asia/Dhaka"),
    ("savar dhaka", "bangladesh"): ("Savar", "Bangladesh", "Asia/Dhaka"),
    ("savar dhaka district", "bangladesh"): ("Savar", "Bangladesh", "Asia/Dhaka"),
    ("dhaka", "bangladesh"): ("Dhaka", "Bangladesh", "Asia/Dhaka"),
}


class InvalidWeatherRequest(ValueError):
    pass


class AmbiguousWeatherLocation(ValueError):
    pass


def _resolve_forecast_date(requested_date: str, today: date) -> date:
    normalized = " ".join(requested_date.casefold().split())
    if normalized in {"today", "tonight", "this evening", "this morning", "this afternoon"}:
        return today
    if normalized == "tomorrow":
        return today + timedelta(days=1)
    if normalized == "the day after tomorrow":
        return today + timedelta(days=2)
    day_match = re.fullmatch(r"(?:next|this)?\s*(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", normalized)
    if day_match:
        days_ahead = (_WEEKDAYS[day_match.group(1)] - today.weekday()) % 7
        if days_ahead == 0 or normalized.startswith("next "):
            days_ahead = days_ahead or 7
            if normalized.startswith("next "):
                days_ahead = days_ahead if days_ahead > 0 else 7
        return today + timedelta(days=days_ahead)
    relative_match = re.fullmatch(
        r"(?:in\s+)?(\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
        r"\s+days?(?:\s+(?:from\s+now|later))?",
        normalized,
    )
    if relative_match:
        amount = relative_match.group(1)
        days = int(amount) if amount.isdigit() else _NUMBER_WORDS[amount]
        return today + timedelta(days=days)
    try:
        return date.fromisoformat(requested_date)
    except ValueError as exc:
        raise InvalidWeatherRequest(
            "Use today, tomorrow, a weekday, a relative day count, or YYYY-MM-DD."
        ) from exc


def _resolve_requested_time(requested_time: str) -> time:
    value = requested_time.strip().upper()
    for time_format in ("%I:%M %p", "%I %p", "%H:%M", "%H"):
        try:
            return datetime.strptime(value, time_format).time()
        except ValueError:
            continue
    raise InvalidWeatherRequest("Use a time such as 3 PM or 15:00.")


def _include_requested_hour(
    payload: dict[str, Any],
    requested_time: str | None,
) -> dict[str, Any]:
    if not requested_time:
        return payload
    requested_clock = _resolve_requested_time(requested_time)
    requested_date = date.fromisoformat(payload["date"])
    requested_datetime = datetime.combine(requested_date, requested_clock)
    hourly = payload["hourly"]
    if not hourly:
        raise ValueError("The weather service returned no hourly forecast for that date.")
    selected = min(
        hourly,
        key=lambda entry: abs(
            datetime.fromisoformat(entry["time"]) - requested_datetime
        ),
    )
    result = dict(payload)
    result["requested_time"] = requested_time
    result["requested_hour"] = selected
    return result


class WeatherService:
    def __init__(
        self,
        db_path: str | Path,
        *,
        session: requests.Session | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.db_path = Path(db_path)
        self.session = session or requests.Session()
        self.timeout = max(1.0, min(timeout, 30.0))
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS weather_cache (
                    cache_key TEXT PRIMARY KEY,
                    fetched_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )

    def _geocode(self, city: str, country: str | None) -> dict[str, Any]:
        response = self.session.get(
            GEOCODING_URL,
            params={"name": city, "count": 10, "language": "en", "format": "json"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        candidates = response.json().get("results") or []
        if country:
            matching_country = [
                candidate
                for candidate in candidates
                if candidate.get("country", "").casefold() == country.casefold()
                or candidate.get("country_code", "").casefold() == country.casefold()
            ]
            if not matching_country:
                raise ValueError(f"No weather location for {city} was found in {country}.")
            candidates = matching_country
        exact = [item for item in candidates if item.get("name", "").casefold() == city.casefold()]
        if len(exact) > 1:
            raise AmbiguousWeatherLocation(
                "The location name is ambiguous; provide a more specific area or country."
            )
        if exact:
            candidates = exact
        if not candidates:
            raise ValueError(f"No weather location could be found for {city}.")
        return candidates[0]

    def resolve_location(
        self,
        city: str,
        country: str | None = None,
    ) -> SavedLocation:
        city = city.strip()
        country = country.strip() if country else None
        if not city:
            raise ValueError("Provide a city or area name.")
        if country:
            normalized_city = " ".join(
                re.sub(r"[^a-z0-9]+", " ", city.casefold()).split()
            )
            known_location = _KNOWN_LOCATION_TIMEZONES.get(
                (normalized_city, country.casefold())
            )
            if known_location is not None:
                resolved_city, resolved_country, timezone = known_location
                get_iana_timezone(timezone)
                return SavedLocation(resolved_city, resolved_country, timezone)
        result = self._geocode(city, country)
        resolved_city = str(result.get("name") or "").strip()
        resolved_country = str(result.get("country") or "").strip()
        timezone = str(result.get("timezone") or "").strip()
        if not resolved_city or not resolved_country or not timezone:
            raise ValueError("The location service did not return a complete location and timezone.")
        get_iana_timezone(timezone)
        return SavedLocation(resolved_city, resolved_country, timezone)

    def _cached(self, cache_key: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            row = conn.execute(
                "SELECT fetched_at, payload FROM weather_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if not row:
            return None
        try:
            fetched_at = datetime.fromisoformat(row[0])
        except ValueError:
            return None
        cache_age = datetime.now().astimezone() - fetched_at
        if cache_age < timedelta(0) or cache_age > _CACHE_MAX_AGE:
            return None
        payload = json.loads(row[1])
        payload["cached"] = True
        payload["fetched_at"] = row[0]
        return payload

    def _store_cache(self, cache_key: str, payload: dict[str, Any]) -> None:
        fetched_at = datetime.now().astimezone().isoformat(timespec="seconds")
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute(
                """
                INSERT INTO weather_cache (cache_key, fetched_at, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    fetched_at = excluded.fetched_at,
                    payload = excluded.payload
                """,
                (cache_key, fetched_at, json.dumps(payload)),
            )

    @staticmethod
    def _cache_key(
        city: str,
        country: str,
        timezone_name: str,
        forecast_date: date,
    ) -> str:
        return hashlib.sha256(
            f"{city.casefold()}|{country.casefold()}|{timezone_name}|"
            f"{forecast_date.isoformat()}".encode("utf-8")
        ).hexdigest()

    def forecast(
        self,
        location: SavedLocation | None,
        *,
        requested_date: str = "today",
        requested_time: str | None = None,
        city: str | None = None,
        country: str | None = None,
    ) -> dict[str, Any]:
        transient_city = city.strip() if city else None
        transient_country = country.strip() if country else None
        if transient_city:
            target = self._geocode(transient_city, transient_country)
            display_name = target.get("name", transient_city)
            display_country = target.get("country", transient_country or "")
            timezone_name = target.get("timezone") or "UTC"
        elif location:
            target = None
            display_name = location.city
            display_country = location.country
            timezone_name = location.timezone
        else:
            raise ValueError("Set a confirmed saved location or specify a city for this request.")

        if requested_time:
            _resolve_requested_time(requested_time)
        local_today = datetime.now(get_iana_timezone(timezone_name)).date()
        forecast_date = _resolve_forecast_date(requested_date, local_today)
        day_offset = (forecast_date - local_today).days
        if not 0 <= day_offset <= 15:
            raise InvalidWeatherRequest(
                "Open-Meteo's requested daily forecast is available only from today through 15 days ahead."
            )

        cache_key = self._cache_key(
            display_name,
            display_country,
            timezone_name,
            forecast_date,
        )
        cached = self._cached(cache_key)
        if cached is not None:
            return _include_requested_hour(cached, requested_time)

        if target is None:
            target = self._geocode(display_name, display_country)
        latitude = float(target["latitude"])
        longitude = float(target["longitude"])
        response = self.session.get(
            FORECAST_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "daily": (
                    "weather_code,temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max,precipitation_sum,wind_speed_10m_max"
                ),
                "current": (
                    "temperature_2m,relative_humidity_2m,precipitation,"
                    "weather_code,wind_speed_10m"
                ),
                "hourly": "temperature_2m,relative_humidity_2m,precipitation_probability",
                "forecast_days": 16,
                "timezone": timezone_name,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        raw = response.json()
        dates = raw.get("daily", {}).get("time", [])
        if forecast_date.isoformat() not in dates:
            raise ValueError("The weather service did not return the requested forecast date.")
        index = dates.index(forecast_date.isoformat())
        daily = raw["daily"]
        payload = {
            "success": True,
            "location": {
                "city": display_name,
                "country": display_country,
                "timezone": timezone_name,
            },
            "date": forecast_date.isoformat(),
            "current": raw.get("current") if forecast_date == local_today else None,
            "units": {
                "current": raw.get("current_units", {}),
                "daily": raw.get("daily_units", {}),
                "hourly": raw.get("hourly_units", {}),
            },
            "forecast": {
                "weather_code": daily.get("weather_code", [None])[index],
                "temperature_max_c": daily.get("temperature_2m_max", [None])[index],
                "temperature_min_c": daily.get("temperature_2m_min", [None])[index],
                "precipitation_probability_max_percent": daily.get(
                    "precipitation_probability_max", [None]
                )[index],
                "precipitation_sum_mm": daily.get("precipitation_sum", [None])[index],
                "wind_speed_max_kmh": daily.get("wind_speed_10m_max", [None])[index],
            },
            "hourly": self._hourly_for_date(raw.get("hourly", {}), forecast_date),
            "forecast_not_observed": forecast_date > local_today,
            "daily_values_are_forecasts": True,
            "cached": False,
            "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "provider": "Open-Meteo",
            "attribution": "Weather data by Open-Meteo, CC BY 4.0",
        }
        self._store_cache(cache_key, payload)
        return _include_requested_hour(payload, requested_time)

    @staticmethod
    def _hourly_for_date(hourly: dict[str, Any], requested_date: date) -> list[dict[str, Any]]:
        result = []
        times = hourly.get("time", [])
        for index, timestamp in enumerate(times):
            if not timestamp.startswith(requested_date.isoformat()):
                continue
            result.append(
                {
                    "time": timestamp,
                    "temperature_c": hourly.get("temperature_2m", [None] * len(times))[index],
                    "relative_humidity_percent": hourly.get(
                        "relative_humidity_2m", [None] * len(times)
                    )[index],
                    "precipitation_probability_percent": hourly.get(
                        "precipitation_probability", [None] * len(times)
                    )[index],
                }
            )
        return result

    def clear_cache(self) -> int:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            cursor = conn.execute("DELETE FROM weather_cache")
        return cursor.rowcount
