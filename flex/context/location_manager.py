from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from flex.context.timezones import get_iana_timezone

@dataclass(frozen=True)
class SavedLocation:
    city: str
    country: str
    timezone: str


class LocationManager:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS saved_location (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    city TEXT NOT NULL,
                    country TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    confirmed INTEGER NOT NULL CHECK (confirmed = 1)
                )
                """
            )

    def save(
        self,
        city: str,
        country: str,
        timezone: str,
        *,
        user_confirmed: bool,
    ) -> SavedLocation:
        city = city.strip()
        country = country.strip()
        timezone = timezone.strip()
        if not user_confirmed:
            raise ValueError("The user must explicitly confirm before saving a location.")
        if not city or len(city) > 120 or not country or len(country) > 120:
            raise ValueError("City and country must each contain 1 to 120 characters.")
        if not timezone or len(timezone) > 100:
            raise ValueError("Provide a valid IANA timezone such as Asia/Dhaka.")
        get_iana_timezone(timezone)
        location = SavedLocation(city, country, timezone)
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute(
                """
                INSERT INTO saved_location (id, city, country, timezone, confirmed)
                VALUES (1, ?, ?, ?, 1)
                ON CONFLICT(id) DO UPDATE SET
                    city = excluded.city,
                    country = excluded.country,
                    timezone = excluded.timezone,
                    confirmed = 1
                """,
                (city, country, timezone),
            )
        return location

    def get(self) -> SavedLocation | None:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            row = conn.execute(
                "SELECT city, country, timezone FROM saved_location WHERE id = 1 AND confirmed = 1"
            ).fetchone()
        return SavedLocation(*row) if row else None

    def delete(self) -> bool:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            cursor = conn.execute("DELETE FROM saved_location WHERE id = 1")
        return cursor.rowcount > 0
