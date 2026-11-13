from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def rank_results(results: Sequence[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    return sorted(
        results,
        key=lambda result: (
            float(result.get("relevance_score", 0)),
            bool(result.get("snippet")),
        ),
        reverse=True,
    )[:limit]
