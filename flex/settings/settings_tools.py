from __future__ import annotations

import sqlite3
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
import requests

from flex.security.permissions import PermissionLevel
from flex.settings.settings_manager import SettingsManager
from flex.tools.registry import ToolSpec


class StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GetSettingArgs(StrictArgs):
    name: Literal[
        "study_folder",
        "preferred_location",
        "preferred_timezone",
        "weather_location",
    ]


class UpdateSettingArgs(StrictArgs):
    name: Literal["study_folder", "preferred_timezone"]
    value: str = Field(min_length=1, max_length=32_767)


class ResetSettingArgs(StrictArgs):
    name: Literal["study_folder", "preferred_timezone"]


class StudyFolderArgs(StrictArgs):
    path: str = Field(min_length=1, max_length=32_767)


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
    )


def create_settings_tools(manager: SettingsManager) -> list[ToolSpec]:
    def list_supported_settings() -> dict[str, Any]:
        return {"success": True, "settings": manager.list_supported_settings()}

    def get_setting(name: str) -> dict[str, Any]:
        try:
            value = manager.get_setting(name)
        except (ValueError, OSError, sqlite3.Error) as exc:
            return {"success": False, "error": {"code": "SETTING_READ_FAILED", "message": str(exc)}}
        return {"success": True, "setting": name, "value": value}

    def update_setting(name: str, value: str) -> dict[str, Any]:
        try:
            saved = manager.update_setting(name, value)
        except (ValueError, OSError, sqlite3.Error, requests.RequestException) as exc:
            return {"success": False, "error": {"code": "SETTING_UPDATE_FAILED", "message": str(exc)}}
        return {"success": True, "setting": name, "value": saved}

    def reset_setting(name: str) -> dict[str, Any]:
        try:
            manager.reset_setting(name)
            saved = manager.get_setting(name)
        except (ValueError, OSError, sqlite3.Error) as exc:
            return {"success": False, "error": {"code": "SETTING_RESET_FAILED", "message": str(exc)}}
        return {"success": True, "setting": name, "value": saved}

    def get_study_folder() -> dict[str, Any]:
        return get_setting("study_folder")

    def update_study_folder(path: str) -> dict[str, Any]:
        return update_setting("study_folder", path)

    return [
        _tool(
            "list_supported_settings",
            "List only settings FLEX can actually read or change through registered handlers.",
            StrictArgs,
            list_supported_settings,
        ),
        _tool(
            "get_setting",
            "Read a supported saved FLEX setting. Use this to verify changes and answer current-setting questions.",
            GetSettingArgs,
            get_setting,
        ),
        _tool(
            "update_setting",
            "Update a supported reversible setting. Study folders must exist and be accessible; preferred_timezone must be an IANA timezone and only affects FLEX local-time answers, not Windows.",
            UpdateSettingArgs,
            update_setting,
            PermissionLevel.LOW_RISK,
        ),
        _tool(
            "reset_setting",
            "Remove the saved preferred study folder without deleting files, or clear the FLEX timezone override.",
            ResetSettingArgs,
            reset_setting,
            PermissionLevel.LOW_RISK,
        ),
        _tool(
            "get_study_folder",
            "Read the saved preferred study folder, if set.",
            StrictArgs,
            get_study_folder,
        ),
        _tool(
            "update_study_folder",
            "Set the preferred study folder to an existing, accessible absolute Windows folder path. Does not create folders.",
            StudyFolderArgs,
            update_study_folder,
            PermissionLevel.LOW_RISK,
        ),
    ]
