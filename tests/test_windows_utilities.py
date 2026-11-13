from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import ValidationError

from flex.config import Settings
from flex.security.permissions import PermissionLevel
from flex.tools import windows_utilities
from flex.tools.windows_utilities import (
    BrightnessControlArgs,
    ClipboardControlArgs,
    WindowsUtilityTools,
    create_windows_utility_tools,
)


def _settings(**overrides):
    return Settings(project_root=Path("."), **overrides)


def test_clipboard_access_is_opt_in_and_does_not_touch_clipboard(monkeypatch):
    tools = WindowsUtilityTools(_settings())
    monkeypatch.setattr(
        windows_utilities,
        "_read_clipboard",
        lambda: pytest.fail("disabled clipboard access must not read clipboard data"),
    )

    with pytest.raises(PermissionError, match="disabled"):
        tools.clipboard_control("get")


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        ({"type": "TEXT", "text": "Hello FLEX"}, {"success": True, "type": "TEXT", "text": "Hello FLEX"}),
        ({"type": "IMAGE"}, {"success": True, "type": "IMAGE"}),
        ({"type": "FILES", "file_count": 3}, {"success": True, "type": "FILES", "file_count": 3}),
        ({"type": "OTHER"}, {"success": True, "type": "OTHER"}),
        ({"type": "EMPTY"}, {"success": True, "type": "EMPTY"}),
    ],
)
def test_clipboard_reads_requested_type_only(monkeypatch, snapshot, expected):
    tools = WindowsUtilityTools(_settings(clipboard_access_enabled=True))
    monkeypatch.setattr(windows_utilities, "_read_clipboard", lambda: snapshot)

    assert tools.clipboard_control("get") == expected


def test_clipboard_set_does_not_return_or_log_contents(monkeypatch):
    tools = WindowsUtilityTools(_settings(clipboard_access_enabled=True))
    written = []
    monkeypatch.setattr(windows_utilities, "_write_clipboard_text", written.append)
    monkeypatch.setattr(
        windows_utilities,
        "_read_clipboard",
        lambda: {"type": "TEXT", "text": "private text"},
    )

    result = tools.clipboard_control("set", "private text")

    assert written == ["private text"]
    assert result == {"success": True, "type": "TEXT", "characters": 12}
    assert "private text" not in str(result)


def test_clipboard_copy_sends_copy_shortcut_without_returning_clipboard_text(monkeypatch):
    tools = WindowsUtilityTools(_settings(clipboard_access_enabled=True))
    copies = []
    monkeypatch.setattr(windows_utilities, "_copy_selection", lambda: copies.append(True))

    result = tools.clipboard_control("copy")

    assert result == {"success": True, "action": "copy"}
    assert copies == [True]


def test_clipboard_paste_targets_previous_app_and_sends_shortcut(monkeypatch):
    tools = WindowsUtilityTools(_settings(clipboard_access_enabled=True))
    actions = []
    monkeypatch.setattr(
        windows_utilities,
        "_focus_previous_external_window",
        lambda: actions.append("focus"),
    )
    monkeypatch.setattr(
        windows_utilities,
        "_send_keyboard_shortcut",
        lambda key: actions.append(key),
    )

    result = tools.clipboard_control("paste")

    assert result == {"success": True, "action": "paste"}
    assert actions == ["focus", windows_utilities.VK_V]


def test_clipboard_set_requires_text():
    with pytest.raises(ValidationError):
        ClipboardControlArgs.model_validate({"operation": "set"})


def test_volume_reports_real_output_volume_and_mute_state(monkeypatch):
    class Endpoint:
        volume = 0.4
        muted = True

        def GetMasterVolumeLevelScalar(self):
            return self.volume

        def GetMute(self):
            return self.muted

        def SetMasterVolumeLevelScalar(self, value, _context):
            self.volume = value

    endpoint = Endpoint()
    uninitialized = []
    monkeypatch.setattr(
        windows_utilities,
        "_audio_endpoint",
        lambda: (endpoint, "USB Headphones", lambda: uninitialized.append(True)),
    )
    tools = WindowsUtilityTools(_settings())

    result = tools.volume_control("increase", amount=10)

    assert result == {
        "success": True,
        "volume": 50,
        "muted": True,
        "output": "USB Headphones",
    }
    assert uninitialized == [True]


def test_volume_set_preserves_mute_state_and_clamps_increase(monkeypatch):
    class Endpoint:
        volume = 0.95
        muted = False

        def GetMasterVolumeLevelScalar(self):
            return self.volume

        def GetMute(self):
            return self.muted

        def SetMasterVolumeLevelScalar(self, value, _context):
            self.volume = value

    endpoint = Endpoint()
    monkeypatch.setattr(
        windows_utilities,
        "_audio_endpoint",
        lambda: (endpoint, "Speakers", lambda: None),
    )

    result = WindowsUtilityTools(_settings()).volume_control("increase", amount=10)

    assert result["volume"] == 100
    assert result["muted"] is False


@pytest.mark.parametrize(
    ("operation", "expected_muted"),
    [("mute", True), ("unmute", False)],
)
def test_volume_mute_actions_verify_mute_separately(
    monkeypatch,
    operation,
    expected_muted,
):
    class Endpoint:
        muted = not expected_muted

        def GetMasterVolumeLevelScalar(self):
            return 0.45

        def GetMute(self):
            return self.muted

        def SetMute(self, value, _context):
            self.muted = bool(value)

    endpoint = Endpoint()
    monkeypatch.setattr(
        windows_utilities,
        "_audio_endpoint",
        lambda: (endpoint, "Speakers", lambda: None),
    )

    result = WindowsUtilityTools(_settings()).volume_control(operation)

    assert result["volume"] == 45
    assert result["muted"] is expected_muted


def test_audio_endpoint_uses_pycaw_default_device_interface(monkeypatch):
    endpoint = object()

    class Device:
        FriendlyName = "Default Headphones"
        EndpointVolume = endpoint

    pycaw_module = ModuleType("pycaw.pycaw")
    pycaw_module.AudioUtilities = type(
        "AudioUtilities",
        (),
        {"GetSpeakers": staticmethod(lambda: Device())},
    )
    comtypes_module = ModuleType("comtypes")
    comtypes_module.CoInitialize = lambda: None
    uninitialized = []
    comtypes_module.CoUninitialize = lambda: uninitialized.append(True)
    monkeypatch.setitem(sys.modules, "pycaw.pycaw", pycaw_module)
    monkeypatch.setitem(sys.modules, "comtypes", comtypes_module)
    monkeypatch.setattr(windows_utilities, "_require_windows", lambda: None)

    actual_endpoint, output_name, cleanup = windows_utilities._audio_endpoint()
    cleanup()

    assert actual_endpoint is endpoint
    assert output_name == "Default Headphones"
    assert uninitialized == [True]


def test_brightness_uses_configured_preset_and_verifies_readback(monkeypatch):
    class Brightness:
        value = 30

        def get_brightness(self, display):
            assert display == 0
            return [self.value]

        def set_brightness(self, value, display):
            assert display == 0
            self.value = value

    backend = Brightness()
    monkeypatch.setattr(windows_utilities, "_brightness_backend", lambda: backend)
    tools = WindowsUtilityTools(_settings(brightness_high=80))

    result = tools.brightness_control("set", preset="high")

    assert result == {"success": True, "brightness": 80, "display": "primary"}


def test_brightness_reports_unsupported_display(monkeypatch):
    class Brightness:
        def get_brightness(self, display):
            raise OSError("no controllable display")

    monkeypatch.setattr(windows_utilities, "_brightness_backend", lambda: Brightness())

    with pytest.raises(RuntimeError, match="can't control this monitor's brightness"):
        WindowsUtilityTools(_settings()).brightness_control("get")


def test_brightness_handles_unreported_primary_display(monkeypatch):
    class Brightness:
        def get_brightness(self, display):
            return [None]

    monkeypatch.setattr(windows_utilities, "_brightness_backend", lambda: Brightness())

    with pytest.raises(RuntimeError, match="can't control this monitor's brightness"):
        WindowsUtilityTools(_settings()).brightness_control("get")


def test_brightness_rejects_failed_readback(monkeypatch):
    class Brightness:
        def get_brightness(self, display):
            return [20]

        def set_brightness(self, value, display):
            pass

    monkeypatch.setattr(windows_utilities, "_brightness_backend", lambda: Brightness())

    with pytest.raises(RuntimeError, match="reported brightness 20%"):
        WindowsUtilityTools(_settings()).brightness_control("set", percent=80)


def test_brightness_set_requires_exactly_one_value():
    with pytest.raises(ValidationError):
        BrightnessControlArgs.model_validate({"operation": "set"})
    with pytest.raises(ValidationError):
        BrightnessControlArgs.model_validate(
            {"operation": "set", "percent": 40, "preset": "medium"}
        )


def test_screenshot_saves_timestamped_local_images_without_overwriting(
    tmp_path: Path,
    monkeypatch,
):
    class FakeImage:
        width = 1280
        height = 720

        def save(self, path, format):
            assert format == "PNG"
            Path(path).write_bytes(b"png")

    image_grab = ModuleType("PIL.ImageGrab")
    captures = []

    def grab(**kwargs):
        captures.append(kwargs)
        return FakeImage()

    image_grab.grab = grab
    pil = ModuleType("PIL")
    pil.__path__ = []
    monkeypatch.setitem(sys.modules, "PIL", pil)
    monkeypatch.setitem(sys.modules, "PIL.ImageGrab", image_grab)
    monkeypatch.setattr(windows_utilities, "_require_windows", lambda: None)
    destination = tmp_path / "Pictures" / "FLEX Screenshots"
    tools = WindowsUtilityTools(_settings(screenshot_directory=str(destination)))

    first = tools.screenshot("full_screen")
    second = tools.screenshot("full_screen")

    assert first["success"] is True
    assert second["success"] is True
    assert first["path"] != second["path"]
    assert Path(first["path"]).is_file()
    assert Path(second["path"]).is_file()
    assert all(capture == {"all_screens": False} for capture in captures)


def test_screenshot_rejects_paths_outside_user_profile():
    with pytest.raises(ValueError, match="inside the current user's profile"):
        windows_utilities._safe_screenshot_directory(
            str(Path.home().parent / "outside"),
            "",
        )


def test_screenshot_application_requires_active_window_action():
    with pytest.raises(ValidationError):
        windows_utilities.ScreenshotArgs.model_validate(
            {"action": "full_screen", "application": "Chrome"}
        )


def test_windows_utility_tools_are_registered_as_low_risk():
    tools = create_windows_utility_tools(_settings())

    assert {tool.name for tool in tools} == {
        "clipboard_control",
        "volume_control",
        "brightness_control",
        "screenshot",
    }
    assert all(tool.permission_level == PermissionLevel.LOW_RISK for tool in tools)
    assert all(tool.verification is not None for tool in tools)
