from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

import requests

from flex.security.redaction import redact_url


@dataclass(frozen=True)
class SearchResult:
    title: str
    snippet: str
    url: str
    domain: str


class SearchBackend(Protocol):
    def search(self, query: str, *, limit: int = 8) -> list[SearchResult]: ...


class _YahooParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[SearchResult] = []
        self._active = False
        self._container_depth = 0
        self._title_link = False
        self._capture_title = False
        self._capture_snippet = False
        self._snippet_depth = 0
        self._href = ""
        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        if tag == "div" and "algo" in classes and not self._active:
            self._active = True
            self._container_depth = 1
            self._href = ""
            self._title_parts = []
            self._snippet_parts = []
            return
        if not self._active:
            return
        if tag == "div":
            self._container_depth += 1
        if tag == "a" and attributes.get("data-matarget") == "algo":
            self._title_link = True
            self._href = attributes.get("href") or ""
        elif tag == "h3" and self._title_link:
            self._capture_title = True
        if tag == "div" and "compText" in classes:
            self._capture_snippet = True
            self._snippet_depth = 1
        elif self._capture_snippet and tag == "div":
            self._snippet_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if not self._active:
            return
        if tag == "h3":
            self._capture_title = False
        elif tag == "a" and self._title_link:
            self._title_link = False
        if tag == "div":
            if self._capture_snippet:
                self._snippet_depth -= 1
                self._capture_snippet = self._snippet_depth > 0
            self._container_depth -= 1
            if self._container_depth == 0:
                self._finish_result()

    def handle_data(self, data: str) -> None:
        if self._capture_title:
            self._title_parts.append(data)
        if self._capture_snippet:
            self._snippet_parts.append(data)

    def close(self) -> None:
        super().close()
        if self._active:
            self._finish_result()

    def _finish_result(self) -> None:
        title = " ".join(" ".join(self._title_parts).split())
        snippet = " ".join(" ".join(self._snippet_parts).split())
        url = _result_url(self._href)
        if title and url:
            self.results.append(
                SearchResult(title, snippet[:1000], url, urlsplit(url).hostname or "")
            )
        self._active = False
        self._container_depth = 0
        self._title_link = False
        self._capture_title = False
        self._capture_snippet = False
        self._snippet_depth = 0


def _result_url(raw_url: str) -> str | None:
    candidate = raw_url.strip()
    parsed = urlsplit(candidate)
    if parsed.hostname and parsed.hostname.casefold() in {
        "duckduckgo.com",
        "www.duckduckgo.com",
    }:
        candidate = parse_qs(parsed.query).get("uddg", [""])[0]
        parsed = urlsplit(candidate)
    elif parsed.hostname and parsed.hostname.casefold() == "r.search.yahoo.com":
        match = re.search(r"/RU=(.*?)(?:/RK=|$)", parsed.path, re.IGNORECASE)
        candidate = unquote(match.group(1)) if match else ""
        parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    hostname = parsed.hostname.casefold().rstrip(".")
    if hostname in {
        "localhost",
        "localhost.localdomain",
        "duckduckgo.com",
        "www.duckduckgo.com",
        "r.search.yahoo.com",
    } or hostname.endswith((".local", ".internal", ".localhost")):
        return None
    try:
        if not ipaddress.ip_address(hostname).is_global:
            return None
    except ValueError:
        if "." not in hostname:
            return None
    try:
        parsed.port
    except ValueError:
        return None
    return redact_url(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")))


class SearchProvider:
    """Fetch external Yahoo search snippets; page content remains untrusted."""

    def __init__(self, *, timeout: float = 12.0, session: requests.Session | None = None) -> None:
        self.timeout = max(1.0, min(timeout, 30.0))
        self.session = session or requests.Session()

    def search(self, query: str, *, limit: int = 8) -> list[SearchResult]:
        if not query.strip() or len(query) > 500:
            raise ValueError("Search query must contain 1 to 500 characters.")
        if not 1 <= limit <= 20:
            raise ValueError("Search result limit must be between 1 and 20.")
        response = self.session.get(
            "https://search.yahoo.com/search",
            params={"p": query.strip()},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        html = response.text[:2_000_000]
        parser = _YahooParser()
        parser.feed(html)
        parser.close()
        if not parser.results and any(
            marker in html.casefold()
            for marker in ("captcha", "unusual traffic", "challenge", "anomaly.js")
        ):
            raise requests.RequestException(
                "Yahoo Search returned a challenge page instead of search results."
            )
        results: list[SearchResult] = []
        seen: set[str] = set()
        for result in parser.results:
            normalized = result.url.casefold().rstrip("/")
            if normalized in seen:
                continue
            seen.add(normalized)
            results.append(result)
            if len(results) >= limit:
                break
        return results


def clean_query_terms(value: str) -> list[str]:
    return re.findall(r"[\w'-]+", value.casefold())
