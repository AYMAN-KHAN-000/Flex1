from __future__ import annotations


class Verifier:
    @staticmethod
    def verify(tool_name: str, result: dict, verification: callable | None = None) -> bool:
        if verification is None:
            return bool(result)
        return bool(verification(**result)) if isinstance(result, dict) else bool(verification(result))
