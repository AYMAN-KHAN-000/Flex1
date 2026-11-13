from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passphrase|api[_-]?key|secret|token|authorization|cookie)"
    r"(\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+\S+")
_KNOWN_TOKEN = re.compile(
    r"(?i)\b(?:sk-or-v1-[A-Za-z0-9_-]+|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})\b"
)
_SENSITIVE_QUERY_KEY = re.compile(
    r"(?i)(password|passphrase|api[_-]?key|secret|token|authorization|cookie|"
    r"access[_-]?token|refresh[_-]?token|session|state|nonce|jwt|credential|^code$)"
)


def redact_sensitive_text(value: str) -> str:
    redacted = _BEARER_TOKEN.sub("Bearer [REDACTED]", value)
    redacted = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", redacted)
    return _KNOWN_TOKEN.sub("[REDACTED]", redacted)


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    safe_query = [
        (key, redact_sensitive_text(value))
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _SENSITIVE_QUERY_KEY.search(key)
    ]
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(safe_query), "")
    )
