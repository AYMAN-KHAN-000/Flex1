from __future__ import annotations

import ctypes
import csv
import os
import subprocess
import time
import urllib.parse
import webbrowser
from ctypes import wintypes
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from flex.security.permissions import PermissionLevel
from flex.tools.app_control import _lookup_application
from flex.tools.registry import ToolSpec


class SearchWebArgs(BaseModel):
    query: str = Field(..., min_length=1, description="Words or a question to search the web for.")
    browser: str = Field(
        default="default",
        description="Browser to use, such as default, chrome, or edge.",
    )


class OpenWebsiteArgs(BaseModel):
    website: str = Field(
        ...,
        min_length=1,
        description="Website URL or site name, such as microsoft.com or YouTube.",
    )
    browser: str = Field(
        default="default",
        description="Browser to use, such as default, chrome, or edge.",
    )


class OpenSocialAccountArgs(BaseModel):
    platform: str = Field(
        ...,
        min_length=1,
        description="Social platform, such as YouTube, Instagram, TikTok, X, or Facebook.",
    )
    account: str = Field(
        ...,
        min_length=1,
        description="Profile handle, profile URL, or account display name.",
    )
    browser: str = Field(
        default="default",
        description="Browser to use, such as default, chrome, or edge.",
    )


class TypeTextArgs(BaseModel):
    application: str = Field(..., min_length=1, description="Open application to type into.")
    text: str = Field(..., description="Text to type into the application's active window.")


def _website_url(website: str) -> str:
    cleaned_website = website.strip().rstrip(".,;")
    if not cleaned_website or any(character.isspace() for character in cleaned_website):
        raise ValueError("A website URL or site name without spaces is required.")

    if "://" not in cleaned_website:
        if cleaned_website.startswith("www.") or "." in cleaned_website:
            cleaned_website = f"https://{cleaned_website}"
        else:
            cleaned_website = f"https://{cleaned_website}.com"

    parsed = urllib.parse.urlsplit(cleaned_website)
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only valid HTTP or HTTPS websites can be opened.")
    if parsed.username or parsed.password:
        raise ValueError("Website URLs containing credentials cannot be opened.")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("The website URL contains an invalid port.") from exc
    if (
        parsed.hostname.casefold() in {"youtube.com", "www.youtube.com", "m.youtube.com"}
        and parsed.path.casefold().startswith("/c/")
    ):
        channel_name = parsed.path[3:].split("/", 1)[0]
        if channel_name:
            return "https://www.youtube.com/results?" + urllib.parse.urlencode(
                {"search_query": channel_name}
            )
    return cleaned_website


_SOCIAL_PLATFORMS = {
    "youtube": "youtube",
    "instagram": "instagram",
    "tiktok": "tiktok",
    "x": "x",
    "twitter": "x",
    "facebook": "facebook",
    "twitch": "twitch",
    "threads": "threads",
}
_SOCIAL_PROFILE_HOSTS = {
    "youtube": "www.youtube.com",
    "instagram": "www.instagram.com",
    "tiktok": "www.tiktok.com",
    "x": "x.com",
    "facebook": "www.facebook.com",
    "twitch": "www.twitch.tv",
    "threads": "www.threads.net",
}


def _social_account_url(platform: str, account: str) -> str:
    cleaned_platform = platform.strip().casefold()
    normalized_platform = _SOCIAL_PLATFORMS.get(cleaned_platform)
    if normalized_platform is None:
        supported = ", ".join(sorted(_SOCIAL_PLATFORMS))
        raise ValueError(f"Unsupported social platform '{platform}'. Supported platforms: {supported}.")

    cleaned_account = account.strip()
    if not cleaned_account:
        raise ValueError("A social account name, handle, or URL is required.")

    parsed_account = urllib.parse.urlsplit(
        cleaned_account if "://" in cleaned_account else ""
    )
    expected_host = _SOCIAL_PROFILE_HOSTS[normalized_platform]
    if parsed_account.scheme:
        if (
            parsed_account.scheme.casefold() not in {"http", "https"}
            or parsed_account.hostname is None
            or parsed_account.hostname.casefold() not in {
                expected_host,
                expected_host.removeprefix("www."),
            }
            or parsed_account.username
            or parsed_account.password
        ):
            raise ValueError(f"The account URL is not a valid {platform} profile URL.")
        if (
            normalized_platform == "youtube"
            and parsed_account.path.casefold().startswith("/c/")
        ):
            channel_name = parsed_account.path[3:].split("/", 1)[0]
            if channel_name:
                return "https://www.youtube.com/results?" + urllib.parse.urlencode(
                    {"search_query": channel_name}
                )
        return cleaned_account

    handle = cleaned_account.removeprefix("@").strip("/")
    if not handle:
        raise ValueError("A social account name or handle is required.")
    if not cleaned_account.startswith("@"):
        search_query = handle
    else:
        profile_paths = {
            "youtube": f"/@{urllib.parse.quote(handle, safe='._-')}",
            "instagram": f"/{urllib.parse.quote(handle, safe='._-')}/",
            "tiktok": f"/@{urllib.parse.quote(handle, safe='._-')}",
            "x": f"/{urllib.parse.quote(handle, safe='._-')}",
            "facebook": f"/{urllib.parse.quote(handle, safe='._-')}",
            "twitch": f"/{urllib.parse.quote(handle, safe='._-')}",
            "threads": f"/@{urllib.parse.quote(handle, safe='._-')}",
        }
        return f"https://{expected_host}{profile_paths[normalized_platform]}"

    search_urls = {
        "youtube": "https://www.youtube.com/results",
        "instagram": "https://www.instagram.com/explore/search/keyword/",
        "tiktok": "https://www.tiktok.com/search",
        "x": "https://x.com/search",
        "facebook": "https://www.facebook.com/search/top/",
        "twitch": "https://www.twitch.tv/search",
        "threads": "https://www.threads.net/search",
    }
    query_key = "search_query" if normalized_platform == "youtube" else "q"
    return f"{search_urls[normalized_platform]}?{urllib.parse.urlencode({query_key: search_query})}"


def open_social_account_executor(
    platform: str,
    account: str,
    browser: str = "default",
) -> dict[str, object]:
    url = _social_account_url(platform, account)
    if browser.strip().casefold() == "default":
        opened = webbrowser.open(url, new=2)
        if not opened:
            raise RuntimeError("Windows could not open the default browser.")
        return {
            "status": "opened",
            "platform": platform,
            "account": account,
            "browser": "default",
            "url": url,
        }

    resolved = _lookup_application(browser)
    if not resolved:
        raise FileNotFoundError(f"Browser '{browser}' was not found.")
    if resolved.lower().endswith(".lnk") or resolved.startswith("shell:AppsFolder\\"):
        raise RuntimeError(f"Browser '{browser}' cannot be passed a social account URL.")
    process = subprocess.Popen([resolved, url], shell=False)
    return {
        "status": "opened",
        "platform": platform,
        "account": account,
        "browser": browser,
        "url": url,
        "process_id": process.pid,
    }


def open_website_executor(website: str, browser: str = "default") -> dict[str, object]:
    url = _website_url(website)
    if browser.strip().casefold() == "default":
        opened = webbrowser.open(url, new=2)
        if not opened:
            raise RuntimeError("Windows could not open the default browser.")
        return {"status": "opened", "website": website, "browser": "default", "url": url}

    resolved = _lookup_application(browser)
    if not resolved:
        raise FileNotFoundError(f"Browser '{browser}' was not found.")
    if resolved.lower().endswith(".lnk") or resolved.startswith("shell:AppsFolder\\"):
        raise RuntimeError(f"Browser '{browser}' cannot be passed a website URL.")
    process = subprocess.Popen([resolved, url], shell=False)
    return {
        "status": "opened",
        "website": website,
        "browser": browser,
        "url": url,
        "process_id": process.pid,
    }


def search_web_executor(query: str, browser: str = "default") -> dict[str, object]:
    cleaned_query = query.strip()
    if not cleaned_query:
        raise ValueError("Search query cannot be empty.")
    url = "https://www.google.com/search?" + urllib.parse.urlencode({"q": cleaned_query})

    if browser.strip().casefold() == "default":
        opened = webbrowser.open(url, new=2)
        if not opened:
            raise RuntimeError("Windows could not open the default browser.")
        return {"status": "opened", "query": cleaned_query, "browser": "default", "url": url}

    resolved = _lookup_application(browser)
    if not resolved:
        raise FileNotFoundError(f"Browser '{browser}' was not found.")
    if resolved.lower().endswith(".lnk") or resolved.startswith("shell:AppsFolder\\"):
        raise RuntimeError(f"Browser '{browser}' cannot be passed a search URL.")
    process = subprocess.Popen([resolved, url], shell=False)
    return {
        "status": "opened",
        "query": cleaned_query,
        "browser": browser,
        "url": url,
        "process_id": process.pid,
    }


def _window_process_ids(application: str, executable: str) -> set[int]:
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Could not locate the '{application}' window: {exc}") from exc

    image_names = {Path(executable).name.casefold()}
    normalized_app = "".join(char for char in application.casefold() if char.isalnum())
    if normalized_app:
        image_names.add(f"{normalized_app}.exe")
    process_ids: set[int] = set()
    for row in csv.reader(result.stdout.splitlines()):
        if len(row) > 1 and row[0].casefold() in image_names and row[1].isdigit():
            process_ids.add(int(row[1]))
    return process_ids


def _type_text_into_application(application: str, text: str) -> dict[str, object]:
    if os.name != "nt":
        raise OSError("Typing into desktop applications is only supported on Windows.")

    resolved = _lookup_application(application)
    if not resolved:
        raise FileNotFoundError(f"Application '{application}' was not found.")

    process_ids: set[int] = set()
    user32 = ctypes.windll.user32
    window_handles: list[int] = []
    callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
    enum_callback = callback_type(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    normalized_app = "".join(char for char in application.casefold() if char.isalnum())

    def visit_window(hwnd: int, _parameter: int) -> bool:
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if not user32.IsWindowVisible(hwnd):
            return True
        title_buffer = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
        normalized_title = "".join(char for char in title_buffer.value.casefold() if char.isalnum())
        if process_id.value in process_ids or (normalized_app and normalized_app in normalized_title):
            window_handles.append(hwnd)
        return True

    callback = enum_callback(visit_window)
    deadline = time.monotonic() + 10
    refresh_processes_at = 0.0
    while not window_handles and time.monotonic() < deadline:
        now = time.monotonic()
        if now >= refresh_processes_at:
            process_ids = _window_process_ids(application, resolved)
            refresh_processes_at = now + 1
        user32.EnumWindows(callback, 0)
        if not window_handles:
            time.sleep(0.25)
    if not window_handles:
        raise RuntimeError(f"No open window was found for '{application}'.")

    window = window_handles[0]
    user32.ShowWindow(window, 9)
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
    if process_id.value:
        user32.AllowSetForegroundWindow(process_id.value)
    if not user32.SetForegroundWindow(window):
        raise RuntimeError(f"Could not focus the '{application}' window.")
    time.sleep(0.2)

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

    keyboard_input = 1
    unicode_flag = 0x0004
    key_up_flag = 0x0002
    inputs = []
    for character in text.replace("\r\n", "\n").replace("\r", "\n"):
        if character in "\r\n":
            key_down = Input()
            key_down.type = keyboard_input
            key_down.keyboard.virtual_key = 0x0D
            key_up = Input()
            key_up.type = keyboard_input
            key_up.keyboard.virtual_key = 0x0D
            key_up.keyboard.flags = key_up_flag
            inputs.extend((key_down, key_up))
            continue
        encoded = character.encode("utf-16-le")
        for index in range(0, len(encoded), 2):
            code_unit = int.from_bytes(encoded[index : index + 2], "little")
            key_down = Input()
            key_down.type = keyboard_input
            key_down.keyboard.scan_code = code_unit
            key_down.keyboard.flags = unicode_flag
            key_up = Input()
            key_up.type = keyboard_input
            key_up.keyboard.scan_code = code_unit
            key_up.keyboard.flags = unicode_flag | key_up_flag
            inputs.extend((key_down, key_up))

    if inputs:
        sent = user32.SendInput(len(inputs), (Input * len(inputs))(*inputs), ctypes.sizeof(Input))
        if sent != len(inputs):
            raise OSError(f"Windows accepted {sent} of {len(inputs)} keyboard events.")
    return {
        "status": "typed",
        "application": application,
        "characters": len(text),
    }


def type_text_executor(application: str, text: str) -> dict[str, object]:
    return _type_text_into_application(application, text)


def _search_web_verifier(_arguments: dict[str, Any], result: dict[str, Any]) -> bool:
    return result.get("status") == "opened" and bool(result.get("url"))


def _open_website_verifier(_arguments: dict[str, Any], result: dict[str, Any]) -> bool:
    return result.get("status") == "opened" and bool(result.get("url"))


def _open_social_account_verifier(_arguments: dict[str, Any], result: dict[str, Any]) -> bool:
    return result.get("status") == "opened" and bool(result.get("url"))


def _type_text_verifier(_arguments: dict[str, Any], result: dict[str, Any]) -> bool:
    return result.get("status") == "typed" and isinstance(result.get("characters"), int)


SEARCH_WEB_TOOL = ToolSpec(
    name="search_web",
    description=(
        "Search the web for a query in the default browser or a named browser such as Chrome or Edge."
    ),
    schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "browser": {
                "type": "string",
                "default": "default",
                "description": "Browser to use, such as default, chrome, or edge.",
            },
        },
        "required": ["query"],
    },
    permission_level=PermissionLevel.LOW_RISK,
    executor=search_web_executor,
    argument_model=SearchWebArgs,
    verification=_search_web_verifier,
)

OPEN_WEBSITE_TOOL = ToolSpec(
    name="open_website",
    description=(
        "Open a website directly in the default browser or a named browser such as Chrome or Edge. "
        "Accepts a complete HTTP(S) URL or a site name such as YouTube."
    ),
    schema={
        "type": "object",
        "properties": {
            "website": {"type": "string", "minLength": 1},
            "browser": {
                "type": "string",
                "default": "default",
                "description": "Browser to use, such as default, chrome, or edge.",
            },
        },
        "required": ["website"],
    },
    permission_level=PermissionLevel.LOW_RISK,
    executor=open_website_executor,
    argument_model=OpenWebsiteArgs,
    verification=_open_website_verifier,
)

OPEN_SOCIAL_ACCOUNT_TOOL = ToolSpec(
    name="open_social_account",
    description=(
        "Open a social media profile or channel on YouTube, Instagram, TikTok, X/Twitter, "
        "Facebook, Twitch, or Threads. Use an explicit @handle or profile URL when the "
        "user provides one. For a display name without a confirmed handle, open the "
        "platform's search results instead of guessing a profile URL. This avoids dead links "
        "such as retired YouTube /c/ channel URLs."
    ),
    schema={
        "type": "object",
        "properties": {
            "platform": {
                "type": "string",
                "enum": ["YouTube", "Instagram", "TikTok", "X", "Twitter", "Facebook", "Twitch", "Threads"],
            },
            "account": {
                "type": "string",
                "minLength": 1,
                "description": "Profile handle, profile URL, or account display name.",
            },
            "browser": {
                "type": "string",
                "default": "default",
                "description": "Browser to use, such as default, chrome, or edge.",
            },
        },
        "required": ["platform", "account"],
    },
    permission_level=PermissionLevel.LOW_RISK,
    executor=open_social_account_executor,
    argument_model=OpenSocialAccountArgs,
    verification=_open_social_account_verifier,
)

TYPE_TEXT_TOOL = ToolSpec(
    name="type_text",
    description=(
        "Type the provided text into a named, already-open Windows application window. "
        "Use after opening the application when needed."
    ),
    schema={
        "type": "object",
        "properties": {
            "application": {"type": "string", "description": "Application window to type into."},
            "text": {"type": "string", "description": "Text to type."},
        },
        "required": ["application", "text"],
    },
    permission_level=PermissionLevel.LOW_RISK,
    executor=type_text_executor,
    argument_model=TypeTextArgs,
    verification=_type_text_verifier,
)
