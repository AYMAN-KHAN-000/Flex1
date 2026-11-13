from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings

from flex.context.context_tools import create_context_tools
from flex.context.location_manager import LocationManager
from flex.context.weather_service import WeatherService
from flex.settings.settings_manager import SettingsManager
from flex.settings.settings_tools import create_settings_tools
from flex.tools.file_search import FindFilesTools


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeLocationSession:
    def __init__(self, results=None):
        self.results = results or [
            {
                "name": "Savar",
                "country": "Bangladesh",
                "country_code": "BD",
                "admin1": "Dhaka",
                "latitude": 23.84,
                "longitude": 90.26,
                "timezone": "Asia/Dhaka",
            }
        ]

    def get(self, _url, **_kwargs):
        return FakeResponse({"results": self.results})


def _make_manager(tmp_path, *, search_locations=None, geocoding_results=None):
    settings_path = tmp_path / "flex-settings.ini"
    database_path = tmp_path / "flex.db"
    settings_factory = lambda: QSettings(
        str(settings_path),
        QSettings.Format.IniFormat,
    )
    locations = LocationManager(database_path)
    weather = WeatherService(
        database_path,
        session=FakeLocationSession(geocoding_results),
    )
    search_locations = search_locations if search_locations is not None else []
    manager = SettingsManager(
        settings_factory,
        locations,
        weather,
        search_locations,
        [],
    )
    return manager, locations, weather, settings_factory, search_locations


def _tool_map(manager):
    return {tool.name: tool for tool in create_settings_tools(manager)}


def test_study_folder_update_persists_verifies_and_changes_search_root(tmp_path):
    first = tmp_path / "Study One"
    second = tmp_path / "Study Two"
    first.mkdir()
    second.mkdir()
    (second / "lecture-notes.pdf").write_bytes(b"")
    manager, _locations, _weather, settings_factory, search_locations = _make_manager(
        tmp_path
    )
    tools = _tool_map(manager)

    updated = tools["update_study_folder"].executor(path=str(first))

    assert updated == {
        "success": True,
        "setting": "study_folder",
        "value": str(first.resolve()),
    }
    assert tools["get_study_folder"].executor() == updated
    assert str(first.resolve()) in search_locations
    assert settings_factory().value("study_folder") == str(first.resolve())

    changed = tools["update_setting"].executor(
        name="study_folder",
        value=str(second),
    )

    assert changed["success"] is True
    assert changed["value"] == str(second.resolve())
    assert str(first.resolve()) not in search_locations
    assert str(second.resolve()) in search_locations
    searched = FindFilesTools(search_locations).find_files(
        location=str(second),
        file_type="pdf",
    )
    assert [result["name"] for result in searched["results"]] == [
        "lecture-notes.pdf"
    ]

    restarted, _locations, _weather, _settings_factory, restarted_search = _make_manager(
        tmp_path
    )
    assert restarted.get_setting("study_folder") == str(second.resolve())
    assert str(second.resolve()) in restarted_search


def test_invalid_study_folder_is_rejected_without_creating_it(tmp_path):
    manager, _locations, _weather, settings_factory, _search_locations = _make_manager(
        tmp_path
    )
    missing = tmp_path / "not-created"

    result = _tool_map(manager)["update_study_folder"].executor(path=str(missing))

    assert result["success"] is False
    assert "does not exist" in result["error"]["message"]
    assert not missing.exists()
    assert not settings_factory().contains("study_folder")


def test_supported_settings_and_unsupported_values_are_explicit(tmp_path):
    manager, _locations, _weather, _settings_factory, _search_locations = _make_manager(
        tmp_path
    )

    names = {setting["name"] for setting in manager.list_supported_settings()}

    assert names == {
        "study_folder",
        "preferred_location",
        "preferred_timezone",
        "weather_location",
    }
    assert "theme" not in names
    with pytest.raises(ValueError, match="Unsupported setting"):
        manager.update_setting("theme", "purple")

    result = _tool_map(manager)["update_setting"].executor(
        name="preferred_timezone",
        value="Not/A-Timezone",
    )
    assert result["success"] is False
    assert "Unknown IANA timezone" in result["error"]["message"]


def test_savar_location_is_resolved_and_persisted_with_dhaka_timezone(tmp_path):
    manager, locations, weather, _settings_factory, _search_locations = _make_manager(
        tmp_path,
    )
    weather.session = type(
        "OfflineSession",
        (),
        {
            "get": lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("Known Bangladesh timezone resolution must not need geocoding.")
            )
        },
    )()
    tool = {
        spec.name: spec
        for spec in create_context_tools(
            locations,
            manager.weather,
            settings_manager=manager,
        )
    }["update_saved_location"]

    result = tool.executor(
        city="Savar",
        country="Bangladesh",
        user_confirmed=True,
    )

    assert result["success"] is True
    assert result["location"] == {
        "city": "Savar",
        "country": "Bangladesh",
        "timezone": "Asia/Dhaka",
        "confirmed_by_user": True,
    }
    assert locations.get().timezone == "Asia/Dhaka"
    assert manager.get_setting("preferred_location") == {
        "city": "Savar",
        "country": "Bangladesh",
        "timezone": "Asia/Dhaka",
    }
    assert LocationManager(locations.db_path).get() == locations.get()


@pytest.mark.parametrize(
    ("city", "expected_city"),
    [
        ("Savar", "Savar"),
        ("Savar, Dhaka", "Savar"),
        ("Savar Dhaka District", "Savar"),
        ("Dhaka", "Dhaka"),
    ],
)
def test_known_bangladesh_locations_resolve_timezone_without_geocoding(
    tmp_path,
    city,
    expected_city,
):
    manager, _locations, weather, _settings_factory, _search_locations = _make_manager(
        tmp_path
    )
    weather.session = type(
        "OfflineSession",
        (),
        {
            "get": lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("The known location should be resolved locally.")
            )
        },
    )()

    resolved = weather.resolve_location(city, "Bangladesh")

    assert (resolved.city, resolved.country, resolved.timezone) == (
        expected_city,
        "Bangladesh",
        "Asia/Dhaka",
    )


def test_ambiguous_location_is_not_saved(tmp_path):
    candidates = [
        {
            "name": "Savar",
            "country": "Bangladesh",
            "timezone": "Asia/Dhaka",
        },
        {
            "name": "Savar",
            "country": "Another Country",
            "timezone": "Asia/Dhaka",
        },
    ]
    manager, locations, _weather, _settings_factory, _search_locations = _make_manager(
        tmp_path,
        geocoding_results=candidates,
    )
    tool = {
        spec.name: spec
        for spec in create_context_tools(
            locations,
            manager.weather,
            settings_manager=manager,
        )
    }["update_saved_location"]

    result = tool.executor(city="Savar", user_confirmed=True)

    assert result["success"] is False
    assert "ambiguous" in result["error"]["message"]
    assert locations.get() is None


def test_timezone_change_is_saved_without_changing_system_timezone(tmp_path):
    manager, locations, _weather, _settings_factory, _search_locations = _make_manager(
        tmp_path
    )

    result = _tool_map(manager)["update_setting"].executor(
        name="preferred_timezone",
        value="Asia/Dhaka",
    )

    assert result["success"] is True
    assert result["value"] == "Asia/Dhaka"
    assert locations.get() is None
    assert manager.get_setting("preferred_timezone") == "Asia/Dhaka"

    time_tool = {
        spec.name: spec
        for spec in create_context_tools(
            locations,
            manager.weather,
            settings_manager=manager,
        )
    }["get_local_time"]
    local_time = time_tool.executor()
    assert local_time["preferred_timezone"] == "Asia/Dhaka"

    restarted, _locations, _weather, _settings_factory, _search_locations = _make_manager(
        tmp_path
    )
    assert restarted.get_setting("preferred_timezone") == "Asia/Dhaka"


def test_study_folder_reset_removes_setting_and_search_root(tmp_path):
    folder = tmp_path / "Study"
    folder.mkdir()
    manager, _locations, _weather, settings_factory, search_locations = _make_manager(
        tmp_path
    )
    manager.update_study_folder(str(folder))

    result = _tool_map(manager)["reset_setting"].executor(name="study_folder")

    assert result == {"success": True, "setting": "study_folder", "value": None}
    assert not settings_factory().contains("study_folder")
    assert str(folder.resolve()) not in search_locations
