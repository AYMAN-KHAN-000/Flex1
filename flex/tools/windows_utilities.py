from __future__ import annotations

import ctypes
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from ctypes import wintypes
from pydantic import BaseModel, ConfigDict, Field, model_validator

from flex.config import Settings
from flex.security.permissions import PermissionLevel
from flex.tools.registry import ToolSpec

CF_UNICODETEXT = 13
CF_TEXT = 1
CF_OEMTEXT = 7
CF_BITMAP = 2
CF_DIB = 8
CF_HDROP = 15
CF_DIBV5 = 17
CF_METAFILEPICT = 3
CF_ENHMETAFILE = 14
VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002
GMEM_MOVEABLE = 0x0002


class _StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClipboardControlArgs(_StrictArgs):
    operation: Literal["get", "set", "clear", "paste", "copy"]
    text: str | None = None

    @model_validator(mode="after")
    def require_text_for_set(self) -> "ClipboardControlArgs":
        if self.operation == "set" and self.text is None:
            raise ValueError("Text is required when setting the clipboard.")
        return self


class VolumeControlArgs(_StrictArgs):
    operation: Literal["get", "set", "increase", "decrease", "mute", "unmute"]
    percent: int | None = Field(default=None, ge=0, le=100)
    amount: int = Field(default=10, ge=1, le=100)

    @model_validator(mode="after")
    def require_percent_for_set(self) -> "VolumeControlArgs":
        if self.operation == "set" and self.percent is None:
            raise ValueError("A volume percentage is required for the set operation.")
        return self


class BrightnessControlArgs(_StrictArgs):
    operation: Literal["get", "set", "increase", "decrease"]
    percent: int | None = Field(default=None, ge=0, le=100)
    amount: int = Field(default=10, ge=1, le=100)
    preset: Literal["minimum", "low", "medium", "high", "maximum"] | None = None

    @model_validator(mode="after")
    def validate_set_value(self) -> "BrightnessControlArgs":
        if self.operation == "set":
            if (self.percent is None) == (self.preset is None):
                raise ValueError("Set brightness with exactly one of percent or preset.")
        elif self.percent is not None or self.preset is not None:
            raise ValueError("Percent and preset are only valid for the set operation.")
        return self


class ScreenshotArgs(_StrictArgs):
    action: Literal["full_screen", "active_window"]
    application: str | None = Field(
        default=None,
        description="Optional application name whose visible window should be captured.",
    )
    directory: str | None = Field(
        default=None,
        description="Optional destination directory within the current user's profile.",
    )

    @model_validator(mode="after")
    def application_requires_window_capture(self) -> "ScreenshotArgs":
        if self.application is not None and not self.application.strip():
            raise ValueError("Application name cannot be empty.")
        if self.application is not None and self.action != "active_window":
            raise ValueError("An application can only be specified for an active-window capture.")
        return self


def _require_windows() -> None:
    if os.name != "nt":
        raise OSError("This utility is only supported on Windows.")


def _clipboard_open() -> None:
    _require_windows()
    user32 = ctypes.windll.user32
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    for _ in range(10):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.05)
    raise OSError("Windows clipboard is busy; please try again.")


def _clipboard_formats() -> set[int]:
    user32 = ctypes.windll.user32
    user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
    user32.EnumClipboardFormats.restype = wintypes.UINT
    formats: set[int] = set()
    clipboard_format = 0
    while True:
        clipboard_format = user32.EnumClipboardFormats(clipboard_format)
        if not clipboard_format:
            break
        formats.add(clipboard_format)
    return formats


def _is_registered_image_format(clipboard_format: int) -> bool:
    if clipboard_format < 0xC000:
        return False
    user32 = ctypes.windll.user32
    user32.GetClipboardFormatNameW.argtypes = [
        wintypes.UINT,
        wintypes.LPWSTR,
        ctypes.c_int,
    ]
    user32.GetClipboardFormatNameW.restype = ctypes.c_int
    name_buffer = ctypes.create_unicode_buffer(128)
    if not user32.GetClipboardFormatNameW(
        clipboard_format,
        name_buffer,
        len(name_buffer),
    ):
        return False
    format_name = name_buffer.value.casefold()
    return any(
        image_type in format_name
        for image_type in ("image/", "png", "jpeg", "jfif", "gif", "tiff", "bitmap")
    )


def _clipboard_text() -> str:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    is_unicode = user32.IsClipboardFormatAvailable(CF_UNICODETEXT)
    clipboard_format = CF_UNICODETEXT if is_unicode else (
        CF_TEXT if user32.IsClipboardFormatAvailable(CF_TEXT) else CF_OEMTEXT
    )
    handle = user32.GetClipboardData(clipboard_format)
    if not handle:
        raise OSError("Windows could not read clipboard text.")

    if clipboard_format != CF_UNICODETEXT:
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise OSError("Windows could not access clipboard text.")
        try:
            text = ctypes.string_at(pointer)
            if clipboard_format == CF_TEXT:
                encoding = "mbcs"
            else:
                kernel32.GetOEMCP.restype = wintypes.UINT
                encoding = f"cp{kernel32.GetOEMCP()}"
            return text.decode(encoding, errors="replace")
        finally:
            kernel32.GlobalUnlock(handle)

    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        raise OSError("Windows could not access clipboard text.")
    try:
        return ctypes.wstring_at(pointer)
    finally:
        kernel32.GlobalUnlock(handle)


def _clipboard_file_count() -> int:
    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    handle = user32.GetClipboardData(CF_HDROP)
    if not handle:
        raise OSError("Windows could not read clipboard file information.")
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT
    return int(shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0))


def _read_clipboard() -> dict[str, Any]:
    _clipboard_open()
    try:
        formats = _clipboard_formats()
        if formats.intersection({CF_UNICODETEXT, CF_TEXT, CF_OEMTEXT}):
            return {"type": "TEXT", "text": _clipboard_text()}
        if formats.intersection(
            {CF_BITMAP, CF_DIB, CF_DIBV5, CF_METAFILEPICT, CF_ENHMETAFILE}
        ) or any(_is_registered_image_format(item) for item in formats):
            return {"type": "IMAGE"}
        if CF_HDROP in formats:
            return {"type": "FILES", "file_count": _clipboard_file_count()}
        if formats:
            return {"type": "OTHER"}
        return {"type": "EMPTY"}
    finally:
        user32 = ctypes.windll.user32
        user32.CloseClipboard()


def _write_clipboard_text(text: str) -> None:
    _require_windows()
    encoded = text.encode("utf-16-le") + b"\0\0"
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.restype = wintypes.BOOL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.restype = wintypes.HGLOBAL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE

    memory = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
    if not memory:
        raise ctypes.WinError(ctypes.get_last_error())
    pointer = kernel32.GlobalLock(memory)
    if not pointer:
        kernel32.GlobalFree(memory)
        raise ctypes.WinError(ctypes.get_last_error())

    try:
        ctypes.memmove(pointer, encoded, len(encoded))
    finally:
        kernel32.GlobalUnlock(memory)

    try:
        _clipboard_open()
        try:
            if not user32.EmptyClipboard():
                raise ctypes.WinError(ctypes.get_last_error())
            if not user32.SetClipboardData(CF_UNICODETEXT, memory):
                raise ctypes.WinError(ctypes.get_last_error())
            memory = None
        finally:
            user32.CloseClipboard()
    finally:
        if memory:
            kernel32.GlobalFree(memory)


def _send_keyboard_shortcut(key: int) -> None:
    _require_windows()
    user32 = ctypes.windll.user32
    class KeyboardInput(ctypes.Structure):
        _fields_ = [
            ("virtual_key", wintypes.WORD),
            ("scan_code", wintypes.WORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("extra_info", ctypes.c_size_t),
        ]

    class MouseInput(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouse_data", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("extra_info", ctypes.c_size_t),
        ]

    class InputUnion(ctypes.Union):
        _fields_ = [("keyboard", KeyboardInput), ("mouse", MouseInput)]

    class Input(ctypes.Structure):
        _anonymous_ = ("union",)
        _fields_ = [("type", wintypes.DWORD), ("union", InputUnion)]

    events = (Input * 4)()
    for index, (virtual_key, key_up) in enumerate(
        (
            (VK_CONTROL, False),
            (key, False),
            (key, True),
            (VK_CONTROL, True),
        )
    ):
        events[index].type = 1
        events[index].keyboard.virtual_key = virtual_key
        events[index].keyboard.flags = KEYEVENTF_KEYUP if key_up else 0

    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    sent = user32.SendInput(len(events), events, ctypes.sizeof(Input))
    if sent != len(events):
        raise ctypes.WinError(ctypes.get_last_error())


def _focus_previous_external_window() -> None:
    _require_windows()
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    current_window = user32.GetForegroundWindow()
    process_id = wintypes.DWORD()
    if current_window:
        user32.GetWindowThreadProcessId(current_window, ctypes.byref(process_id))
    if process_id.value != os.getpid():
        return

    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.EnumWindows.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    windows: list[int] = []
    callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
    enum_callback = callback_type(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_callback, wintypes.LPARAM]

    def visit(window: int, _parameter: int) -> bool:
        windows.append(window)
        return True

    callback = enum_callback(visit)
    user32.EnumWindows(callback, 0)
    try:
        flex_window_index = next(
            index
            for index, window in enumerate(windows)
            if window == current_window
        )
    except StopIteration:
        flex_window_index = -1

    target_window = 0
    for window in windows[flex_window_index + 1 :]:
        if not user32.IsWindowVisible(window) or not user32.GetWindowTextLengthW(window):
            continue
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(owner_pid))
        if owner_pid.value != os.getpid():
            target_window = window
            break
    if not target_window:
        raise RuntimeError("No previous application window is available to paste into.")

    user32.ShowWindow(target_window, 9)
    if not user32.SetForegroundWindow(target_window):
        raise RuntimeError("Windows could not focus the previous application for pasting.")


def _paste_clipboard() -> None:
    _focus_previous_external_window()
    _send_keyboard_shortcut(VK_V)


def _copy_selection() -> None:
    _require_windows()
    user32 = ctypes.windll.user32
    user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    initial_sequence = user32.GetClipboardSequenceNumber()
    _send_keyboard_shortcut(0x43)

    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if user32.GetClipboardSequenceNumber() != initial_sequence:
            return
        time.sleep(0.05)
    raise RuntimeError("No new clipboard content appeared after requesting copy.")


def _audio_endpoint() -> tuple[Any, str, Any]:
    _require_windows()
    try:
        from comtypes import CoInitialize, CoUninitialize
        from pycaw.pycaw import AudioUtilities
    except ImportError as exc:
        raise RuntimeError("Volume control requires the installed pycaw audio dependency.") from exc

    CoInitialize()
    try:
        device = AudioUtilities.GetSpeakers()
        endpoint = device.EndpointVolume
        output_name = getattr(device, "FriendlyName", None) or "Default Windows output device"
        return endpoint, output_name, CoUninitialize
    except Exception:
        CoUninitialize()
        raise


def _brightness_backend() -> Any:
    _require_windows()
    try:
        import screen_brightness_control as sbc
    except ImportError as exc:
        raise RuntimeError(
            "Brightness control requires the installed screen-brightness-control dependency."
        ) from exc
    return sbc


def _active_window_bounds(application: str | None = None) -> tuple[int, int, int, int]:
    _require_windows()
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    window = user32.GetForegroundWindow()
    if application:
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        requested = "".join(character for character in application.casefold() if character.isalnum())
        candidates: list[int] = []
        callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
        enum_callback = callback_type(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [enum_callback, wintypes.LPARAM]
        user32.EnumWindows.restype = wintypes.BOOL

        def matches(candidate: int) -> bool:
            if not user32.IsWindowVisible(candidate):
                return False
            title_buffer = ctypes.create_unicode_buffer(
                max(2, user32.GetWindowTextLengthW(candidate) + 1)
            )
            user32.GetWindowTextW(candidate, title_buffer, len(title_buffer))
            title = "".join(
                character for character in title_buffer.value.casefold() if character.isalnum()
            )
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(candidate, ctypes.byref(process_id))
            process = kernel32.OpenProcess(0x1000, False, process_id.value)
            process_name = ""
            if process:
                try:
                    process_path = ctypes.create_unicode_buffer(32768)
                    path_length = wintypes.DWORD(len(process_path))
                    if kernel32.QueryFullProcessImageNameW(
                        process,
                        0,
                        process_path,
                        ctypes.byref(path_length),
                    ):
                        process_name = "".join(
                            character
                            for character in Path(process_path.value).stem.casefold()
                            if character.isalnum()
                        )
                finally:
                    kernel32.CloseHandle(process)
            return requested in title or requested in process_name

        def visit(candidate: int, _parameter: int) -> bool:
            if matches(candidate):
                candidates.append(candidate)
            return True

        callback = enum_callback(visit)
        user32.EnumWindows(callback, 0)
        if window not in candidates:
            window = candidates[0] if candidates else 0
        if not window:
            raise RuntimeError(f"No visible window was found for '{application}'.")
    elif not window or not user32.IsWindowVisible(window):
        raise RuntimeError("There is no visible active window to capture.")

    bounds = wintypes.RECT()
    if not user32.GetWindowRect(window, ctypes.byref(bounds)):
        raise ctypes.WinError(ctypes.get_last_error())
    if bounds.right <= bounds.left or bounds.bottom <= bounds.top:
        raise RuntimeError("The active window has no capturable area.")
    return bounds.left, bounds.top, bounds.right, bounds.bottom


def _safe_screenshot_directory(directory: str | None, configured_directory: str) -> Path:
    home = Path.home().resolve()
    if directory:
        destination = Path(directory).expanduser()
        if not destination.is_absolute():
            destination = home / destination
        destination = destination.resolve()
        if destination != home and home not in destination.parents:
            raise ValueError(
                "Screenshot directories supplied in a request must be inside the current user's profile."
            )
    elif configured_directory:
        destination = Path(configured_directory).expanduser()
        if not destination.is_absolute():
            destination = home / destination
        destination = destination.resolve()
    else:
        destination = home / "Pictures" / "FLEX Screenshots"

    destination.mkdir(parents=True, exist_ok=True)
    return destination


def _screenshot_filename(directory: Path) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    candidate = directory / f"Screenshot_{stamp}.png"
    suffix = 1
    while candidate.exists():
        candidate = directory / f"Screenshot_{stamp}_{suffix:03d}.png"
        suffix += 1
    return candidate


class WindowsUtilityTools:
    def __init__(self, settings: Settings) -> None:
        self.clipboard_access_enabled = settings.clipboard_access_enabled
        self.screenshot_directory = settings.screenshot_directory
        self.screenshot_all_monitors = settings.screenshot_all_monitors
        self.brightness_presets = {
            "minimum": settings.brightness_minimum,
            "low": settings.brightness_low,
            "medium": settings.brightness_medium,
            "high": settings.brightness_high,
            "maximum": settings.brightness_maximum,
        }

    def clipboard_control(self, operation: str, text: str | None = None) -> dict[str, Any]:
        if not self.clipboard_access_enabled:
            raise PermissionError(
                "Clipboard access is disabled. Enable it in FLEX Settings to use clipboard tools."
            )
        if operation == "get":
            return {"success": True, **_read_clipboard()}
        if operation == "set":
            if text is None:
                raise ValueError("Text is required when setting the clipboard.")
            _write_clipboard_text(text)
            actual = _read_clipboard()
            if actual.get("type") != "TEXT" or actual.get("text") != text:
                raise RuntimeError("Windows did not preserve the requested clipboard text.")
            return {"success": True, "type": "TEXT", "characters": len(text)}
        if operation == "clear":
            _require_windows()
            _clipboard_open()
            try:
                if not ctypes.windll.user32.EmptyClipboard():
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                ctypes.windll.user32.CloseClipboard()
            if _read_clipboard().get("type") != "EMPTY":
                raise RuntimeError("Windows clipboard was not empty after the clear request.")
            return {"success": True, "type": "EMPTY"}
        if operation == "paste":
            _paste_clipboard()
            return {"success": True, "action": "paste"}
        if operation == "copy":
            _copy_selection()
            return {"success": True, "action": "copy"}
        raise ValueError(f"Unsupported clipboard operation: {operation}")

    def volume_control(
        self,
        operation: str,
        percent: int | None = None,
        amount: int = 10,
    ) -> dict[str, Any]:
        endpoint, output_name, uninitialize = _audio_endpoint()
        try:
            current = round(endpoint.GetMasterVolumeLevelScalar() * 100)
            if operation == "set":
                if percent is None:
                    raise ValueError("A volume percentage is required for the set operation.")
                endpoint.SetMasterVolumeLevelScalar(percent / 100, None)
            elif operation == "increase":
                endpoint.SetMasterVolumeLevelScalar(min(100, current + amount) / 100, None)
            elif operation == "decrease":
                endpoint.SetMasterVolumeLevelScalar(max(0, current - amount) / 100, None)
            elif operation == "mute":
                endpoint.SetMute(1, None)
            elif operation == "unmute":
                endpoint.SetMute(0, None)
            elif operation != "get":
                raise ValueError(f"Unsupported volume operation: {operation}")

            actual_volume = round(endpoint.GetMasterVolumeLevelScalar() * 100)
            actual_muted = bool(endpoint.GetMute())
            expected = (
                percent
                if operation == "set"
                else min(100, current + amount)
                if operation == "increase"
                else max(0, current - amount)
                if operation == "decrease"
                else None
            )
            if expected is not None and abs(actual_volume - expected) > 1:
                raise RuntimeError(
                    f"Windows reported volume {actual_volume}% after requesting {expected}%."
                )
            if operation == "mute" and not actual_muted:
                raise RuntimeError("Windows did not enable mute.")
            if operation == "unmute" and actual_muted:
                raise RuntimeError("Windows did not disable mute.")
            return {
                "success": True,
                "volume": actual_volume,
                "muted": actual_muted,
                "output": output_name,
            }
        finally:
            uninitialize()

    def brightness_control(
        self,
        operation: str,
        percent: int | None = None,
        amount: int = 10,
        preset: str | None = None,
    ) -> dict[str, Any]:
        sbc = _brightness_backend()
        try:
            current_value = sbc.get_brightness(display=0)
        except Exception as exc:
            raise RuntimeError(
                f"I can't control this monitor's brightness through Windows: {exc}"
            ) from exc
        if not current_value or current_value[0] is None:
            raise RuntimeError("I can't control this monitor's brightness through Windows.")
        current = int(current_value[0] if isinstance(current_value, list) else current_value)
        target = percent
        if preset is not None:
            target = self.brightness_presets[preset]
        if operation == "set":
            if target is None:
                raise ValueError("Set brightness with a percentage or a configured preset.")
            try:
                sbc.set_brightness(target, display=0)
            except Exception as exc:
                raise RuntimeError(
                    f"I can't control this monitor's brightness through Windows: {exc}"
                ) from exc
            expected = target
        elif operation == "increase":
            expected = min(100, current + amount)
            try:
                sbc.set_brightness(expected, display=0)
            except Exception as exc:
                raise RuntimeError(
                    f"I can't control this monitor's brightness through Windows: {exc}"
                ) from exc
        elif operation == "decrease":
            expected = max(0, current - amount)
            try:
                sbc.set_brightness(expected, display=0)
            except Exception as exc:
                raise RuntimeError(
                    f"I can't control this monitor's brightness through Windows: {exc}"
                ) from exc
        elif operation == "get":
            expected = None
        else:
            raise ValueError(f"Unsupported brightness operation: {operation}")

        try:
            actual_value = sbc.get_brightness(display=0)
        except Exception as exc:
            raise RuntimeError(
                f"Windows stopped reporting controllable display brightness: {exc}"
            ) from exc
        if not actual_value or actual_value[0] is None:
            raise RuntimeError("Windows stopped reporting controllable display brightness.")
        actual = int(actual_value[0] if isinstance(actual_value, list) else actual_value)
        if expected is not None and abs(actual - expected) > 2:
            raise RuntimeError(
                f"Windows reported brightness {actual}% after requesting {expected}%."
            )
        return {"success": True, "brightness": actual, "display": "primary"}

    def screenshot(
        self,
        action: str,
        application: str | None = None,
        directory: str | None = None,
    ) -> dict[str, Any]:
        _require_windows()
        try:
            from PIL import ImageGrab
        except ImportError as exc:
            raise RuntimeError("Screenshots require the installed Pillow dependency.") from exc

        destination = _safe_screenshot_directory(directory, self.screenshot_directory)
        image_path = _screenshot_filename(destination)
        try:
            if action == "full_screen":
                if application is not None:
                    raise ValueError("An application can only be specified for an active-window capture.")
                image = ImageGrab.grab(all_screens=self.screenshot_all_monitors)
            elif action == "active_window":
                image = ImageGrab.grab(bbox=_active_window_bounds(application))
            else:
                raise ValueError(f"Unsupported screenshot action: {action}")
            image.save(image_path, format="PNG")
        except Exception as exc:
            if isinstance(exc, (ValueError, RuntimeError)):
                raise
            raise RuntimeError("FLEX could not capture that screen or window.") from exc

        if not image_path.is_file() or image_path.stat().st_size == 0:
            raise RuntimeError("FLEX did not produce a valid screenshot file.")
        return {
            "success": True,
            "action": action,
            "path": str(image_path),
            "width": image.width,
            "height": image.height,
        }


def _success_verifier(_arguments: dict[str, Any], result: dict[str, Any]) -> bool:
    return result.get("success") is True


def create_windows_utility_tools(
    settings: Settings,
    utilities: WindowsUtilityTools | None = None,
) -> list[ToolSpec]:
    utilities = utilities or WindowsUtilityTools(settings)
    return [
        ToolSpec(
            name="clipboard_control",
            description=(
                "Read, set, clear, paste, or copy the current selection using the Windows clipboard. "
                "Only read clipboard contents "
                "when the user explicitly asks. Paste targets the most recently foregrounded "
                "non-FLEX application. Clipboard access must be enabled in FLEX Settings."
            ),
            schema=ClipboardControlArgs.model_json_schema(),
            permission_level=PermissionLevel.LOW_RISK,
            executor=utilities.clipboard_control,
            argument_model=ClipboardControlArgs,
            verification=_success_verifier,
        ),
        ToolSpec(
            name="volume_control",
            description=(
                "Read or control volume on the current/default Windows output device. "
                "For set, provide percent. Increase/decrease default to 10 percent; mute state "
                "is separate from volume."
            ),
            schema=VolumeControlArgs.model_json_schema(),
            permission_level=PermissionLevel.LOW_RISK,
            executor=utilities.volume_control,
            argument_model=VolumeControlArgs,
            verification=_success_verifier,
        ),
        ToolSpec(
            name="brightness_control",
            description=(
                "Read or change brightness on the primary display. Set with a percent or a "
                "configured preset (minimum, low, medium, high, maximum); increase/decrease "
                "default to 10 percent. Reports an error if Windows cannot control the display."
            ),
            schema=BrightnessControlArgs.model_json_schema(),
            permission_level=PermissionLevel.LOW_RISK,
            executor=utilities.brightness_control,
            argument_model=BrightnessControlArgs,
            verification=_success_verifier,
        ),
        ToolSpec(
            name="screenshot",
            description=(
                "Capture the full screen or active window (optionally by application name) and "
                "save a timestamped PNG in the "
                "configured FLEX Screenshots folder. Screenshots are saved locally and are never "
                "sent to the AI for analysis by this tool."
            ),
            schema=ScreenshotArgs.model_json_schema(),
            permission_level=PermissionLevel.LOW_RISK,
            executor=utilities.screenshot,
            argument_model=ScreenshotArgs,
            verification=_success_verifier,
        ),
    ]
