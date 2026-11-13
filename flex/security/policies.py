from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SecurityPolicy:
    allow_filesystem: bool = True
    allow_network: bool = True
    require_confirmation_for_high_risk: bool = True
    allowed_roots: tuple[str, ...] = ("C:/Users", "C:/Users/Downloads", "C:/Users/Desktop")


DEFAULT_POLICY = SecurityPolicy()


def is_within_allowed_root(path: str, roots: tuple[str, ...] = DEFAULT_POLICY.allowed_roots) -> bool:
    candidate = Path(path).resolve(strict=False)
    for root in roots:
        root_path = Path(root).resolve(strict=False)
        if candidate == root_path or root_path in candidate.parents:
            return True
    return False
