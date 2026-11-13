from __future__ import annotations


class Observer:
    """Records runtime observations from tool execution."""

    @staticmethod
    def inspect(result: dict) -> dict:
        return {"ok": bool(result.get("status") in {"ok", "opened", "created", "found", "completed"}), "result": result}
