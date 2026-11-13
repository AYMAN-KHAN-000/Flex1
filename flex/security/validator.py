from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError


class ToolValidationError(ValueError):
    """Raised when a tool request does not match its schema."""


class ArgumentValidator:
    """Simple Pydantic-backed validator for tool requests."""

    @staticmethod
    def validate(schema: type[BaseModel], payload: dict[str, Any]) -> BaseModel:
        try:
            return schema.model_validate(payload)
        except ValidationError as exc:
            details = "; ".join(err.get("msg", "invalid value") for err in exc.errors())
            raise ToolValidationError(details) from exc

    @staticmethod
    def ensure_safe_path(path: str, *, base_dir: str | None = None) -> str:
        import os

        normalized = os.path.normpath(path)
        if normalized.startswith("..") or normalized == "..":
            raise ToolValidationError("Path traversal is not allowed.")

        if base_dir:
            base_path = os.path.normpath(base_dir)
            if os.path.isabs(path):
                if not normalized.startswith(base_path):
                    raise ToolValidationError("Path escapes the allowed root directory.")
        return normalized
