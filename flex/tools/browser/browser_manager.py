from __future__ import annotations

import logging
import queue
import re
import threading
from concurrent.futures import Future
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

from flex.security.redaction import redact_sensitive_text, redact_url

logger = logging.getLogger("flex.browser")
MAX_PAGE_TEXT = 5_000
_ALLOWED_BROWSERS = {"chrome": "chrome", "edge": "msedge", "chromium": None}


class BrowserActionError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class BrowserManager:
    """Own a persistent Playwright context on a dedicated thread."""

    def __init__(
        self,
        *,
        timeout_ms: int = 10_000,
        max_tabs: int = 5,
        default_browser: str = "chrome",
        profile_root: str | Path | None = None,
    ) -> None:
        self.timeout_ms = timeout_ms
        self.max_tabs = max_tabs
        self.default_browser = default_browser
        self.profile_root = Path(profile_root or Path.home() / "AppData" / "Local" / "FLEX" / "browser")
        self._queue: queue.Queue[tuple[str, dict[str, Any], Future[dict[str, Any]]] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._startup_error: dict[str, Any] | None = None

    def _ensure_worker(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._ready.clear()
            self._startup_error = None
            self._thread = threading.Thread(
                target=self._worker,
                name="FLEX-Playwright",
                daemon=True,
            )
            self._thread.start()
        self._ready.wait()

    def _worker(self) -> None:
        try:
            from playwright.sync_api import sync_playwright

            playwright = sync_playwright().start()
        except ImportError:
            self._startup_error = {
                "success": False,
                "error": {
                    "code": "BROWSER_NOT_AVAILABLE",
                    "message": "Playwright is not installed. Install project requirements to enable browser automation.",
                    "retryable": False,
                },
            }
            self._ready.set()
            return
        except Exception as exc:
            logger.exception("Unable to initialize Playwright.")
            self._startup_error = self._error(
                "BROWSER_NOT_AVAILABLE",
                f"Playwright could not initialize: {exc}",
            )
            self._ready.set()
            return

        self._context: Any = None
        self._browser_name: str | None = None
        self._playwright = playwright
        self._ready.set()
        while True:
            command = self._queue.get()
            if command is None:
                self._close_context()
                playwright.stop()
                return
            name, arguments, future = command
            if future.cancelled():
                continue
            try:
                method = getattr(self, f"_do_{name}")
                future.set_result(method(**arguments))
            except BrowserActionError as exc:
                future.set_result(self._error(exc.code, str(exc), retryable=exc.retryable))
            except Exception as exc:
                logger.error(
                    "Browser operation failed operation=%s error_type=%s",
                    name,
                    type(exc).__name__,
                )
                if "Timeout" in type(exc).__name__:
                    future.set_result(
                        self._error(
                            "BROWSER_TIMEOUT",
                            "Browser operation exceeded its configured time limit.",
                            retryable=True,
                        )
                    )
                else:
                    future.set_result(self._error("BROWSER_OPERATION_FAILED", "Browser operation failed."))

    @staticmethod
    def _error(code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
        return {
            "success": False,
            "error": {"code": code, "message": message, "retryable": retryable},
        }

    def _submit(self, name: str, **arguments: Any) -> dict[str, Any]:
        self._ensure_worker()
        if self._startup_error is not None:
            return dict(self._startup_error)
        if self._thread is None or not self._thread.is_alive():
            return self._error("BROWSER_NOT_AVAILABLE", "The Playwright worker is not running.")
        future: Future[dict[str, Any]] = Future()
        self._queue.put((name, arguments, future))
        try:
            return future.result(timeout=self.timeout_ms / 1000 + 10)
        except TimeoutError:
            return self._error("BROWSER_TIMEOUT", "Browser operation exceeded its configured time limit.", retryable=True)

    def _ensure_context(self, browser: str) -> Any:
        channel = _ALLOWED_BROWSERS.get(browser)
        if browser not in _ALLOWED_BROWSERS:
            raise BrowserActionError("BROWSER_NOT_AVAILABLE", f"Unsupported browser: {browser}.")
        if self._context is not None and self._browser_name == browser:
            try:
                if any(not page.is_closed() for page in self._context.pages):
                    return self._context
            except Exception:
                self._close_context()
        if self._context is not None:
            self._close_context()

        profile = self.profile_root / browser
        profile.mkdir(parents=True, exist_ok=True)
        try:
            self._context = self._playwright.chromium.launch_persistent_context(
                str(profile),
                channel=channel,
                headless=False,
                timeout=self.timeout_ms,
            )
        except Exception as exc:
            logger.info("Playwright could not launch browser=%s error_type=%s", browser, type(exc).__name__)
            raise BrowserActionError(
                "BROWSER_NOT_AVAILABLE",
                f"Could not launch {browser}. Install its browser binary or verify the browser installation.",
            ) from exc
        self._browser_name = browser
        self._context.set_default_timeout(self.timeout_ms)
        return self._context

    def _current_page(self) -> Any:
        if self._context is None:
            raise BrowserActionError("BROWSER_TARGET_NOT_FOUND", "No FLEX browser session is open.")
        pages = [page for page in self._context.pages if not page.is_closed()]
        if not pages:
            raise BrowserActionError("BROWSER_TARGET_NOT_FOUND", "No browser tab is open.")
        return pages[-1]

    @staticmethod
    def _validate_url(url: str) -> str:
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise BrowserActionError(
                "BROWSER_INVALID_URL",
                "Use a complete HTTP or HTTPS URL without embedded credentials.",
            )
        return url.strip()

    def _navigate(self, page: Any, url: str) -> dict[str, Any]:
        requested_url = self._validate_url(url)
        try:
            response = page.goto(requested_url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        except Exception as exc:
            if "Timeout" in type(exc).__name__:
                raise BrowserActionError("BROWSER_TIMEOUT", "The page did not become ready within the configured timeout.", retryable=True) from exc
            raise
        current_url = page.url
        parsed = urlparse(current_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise BrowserActionError("BROWSER_VERIFICATION_FAILED", "Navigation did not reach a valid web page.")
        if response is not None and response.status >= 400:
            raise BrowserActionError(
                "BROWSER_NAVIGATION_FAILED",
                f"The destination returned HTTP {response.status}.",
                retryable=response.status >= 500,
            )
        return {
            "success": True,
            "url": redact_url(current_url),
            "title": redact_sensitive_text(page.title()),
            "status": response.status if response is not None else None,
        }

    def _read_page_text(self, page: Any, max_chars: int = MAX_PAGE_TEXT) -> str:
        return page.locator("body").inner_text(timeout=self.timeout_ms)[:max_chars]

    def open(
        self,
        browser: str | None,
        url: str,
        reuse_session: bool = True,
    ) -> dict[str, Any]:
        selected_browser = browser or self.default_browser
        return self._submit("open", browser=selected_browser, url=url, reuse_session=reuse_session)

    def navigate(self, url: str) -> dict[str, Any]:
        return self._submit("navigate", url=url)

    def search(self, query: str, engine: str = "duckduckgo") -> dict[str, Any]:
        return self._submit("search", query=query, engine=engine)

    def click(self, strategy: str, value: str, role: str | None = None, context: str | None = None) -> dict[str, Any]:
        return self._submit("click", strategy=strategy, value=value, role=role, context=context)

    def type_text(self, strategy: str, value: str, text: str, role: str | None = None) -> dict[str, Any]:
        return self._submit("type_text", strategy=strategy, value=value, text=text, role=role)

    def get_page_info(self) -> dict[str, Any]:
        return self._submit("get_page_info")

    def get_text(self, max_chars: int = MAX_PAGE_TEXT) -> dict[str, Any]:
        return self._submit("get_text", max_chars=max_chars)

    def back(self) -> dict[str, Any]:
        return self._submit("back")

    def refresh(self) -> dict[str, Any]:
        return self._submit("refresh")

    def close(self) -> dict[str, Any]:
        result = self._submit("close")
        if self._thread is not None and self._thread.is_alive():
            self._queue.put(None)
            self._thread.join(timeout=5)
        self._thread = None
        return result

    def _do_open(self, browser: str, url: str, reuse_session: bool) -> dict[str, Any]:
        context = self._ensure_context(browser)
        live_pages = [page for page in context.pages if not page.is_closed()]
        if reuse_session and live_pages:
            page = live_pages[-1]
        elif len(live_pages) == 1 and live_pages[0].url == "about:blank":
            page = live_pages[0]
        else:
            if len(live_pages) >= self.max_tabs:
                raise BrowserActionError("BROWSER_TAB_LIMIT", f"FLEX allows at most {self.max_tabs} browser tabs.")
            page = context.new_page()
        return self._navigate(page, url)

    def _do_navigate(self, url: str) -> dict[str, Any]:
        return self._navigate(self._current_page(), url)

    def _do_search(self, query: str, engine: str) -> dict[str, Any]:
        if not query.strip() or len(query) > 500:
            raise BrowserActionError("BROWSER_INVALID_REQUEST", "Search text must contain 1 to 500 characters.")
        search_urls = {
            "google": "https://www.google.com/search",
            "bing": "https://www.bing.com/search",
            "duckduckgo": "https://duckduckgo.com/",
        }
        base_url = search_urls.get(engine)
        if base_url is None:
            raise BrowserActionError("BROWSER_INVALID_REQUEST", "Choose Google, Bing, or DuckDuckGo.")
        separator = "&" if "?" in base_url else "?"
        result = self._navigate(
            self._current_page(),
            f"{base_url}{separator}{urlencode({'q': query})}",
        )
        page = self._current_page()
        try:
            visible_text = self._read_page_text(page, 2_000)
        except Exception as exc:
            if "Timeout" in type(exc).__name__:
                raise BrowserActionError(
                    "BROWSER_TIMEOUT",
                    "Search results did not become visible in time.",
                    retryable=True,
                ) from exc
            raise BrowserActionError(
                "BROWSER_VERIFICATION_FAILED",
                "Search page text could not be verified.",
            ) from exc
        query_terms = [term.casefold() for term in query.split() if len(term) > 1]
        if not visible_text.strip() or (
            query_terms and not any(term in visible_text.casefold() for term in query_terms)
        ):
            raise BrowserActionError(
                "BROWSER_VERIFICATION_FAILED",
                "The page did not show visible results for the requested search.",
            )
        result["query"] = redact_sensitive_text(query)
        result["engine"] = engine
        result["results_observed"] = True
        return result

    def _locator(self, page: Any, strategy: str, value: str, role: str | None) -> Any:
        if strategy == "role":
            if not role:
                raise BrowserActionError("BROWSER_INVALID_REQUEST", "A role is required for accessibility locators.")
            locator = page.get_by_role(role, name=value)
        elif strategy == "label":
            locator = page.get_by_label(value)
        elif strategy == "placeholder":
            locator = page.get_by_placeholder(value)
        elif strategy == "text":
            locator = page.get_by_text(value)
        elif strategy == "test_id":
            locator = page.get_by_test_id(value)
        elif strategy == "css":
            if re.search(r"(?:javascript:|:has-text\(|xpath=|>>)", value, re.IGNORECASE):
                raise BrowserActionError("BROWSER_INVALID_REQUEST", "Unsafe CSS locator syntax is not allowed.")
            locator = page.locator(value)
        else:
            raise BrowserActionError("BROWSER_INVALID_REQUEST", "Unsupported locator strategy.")
        count = locator.count()
        if count == 0:
            try:
                locator.wait_for(state="visible", timeout=self.timeout_ms)
                count = locator.count()
            except Exception as exc:
                if "Timeout" in type(exc).__name__:
                    raise BrowserActionError(
                        "BROWSER_TARGET_NOT_FOUND",
                        "The requested page element was not found.",
                    ) from exc
                raise
            if count == 0:
                raise BrowserActionError("BROWSER_TARGET_NOT_FOUND", "The requested page element was not found.")
        if count > 1:
            raise BrowserActionError(
                "AMBIGUOUS_TARGET",
                f"The locator matched {count} elements. Add role, label, or nearby text to identify one.",
            )
        return locator

    def _do_click(self, strategy: str, value: str, role: str | None, context: str | None) -> dict[str, Any]:
        page = self._current_page()
        locator = self._locator(page, strategy, value, role)
        if context:
            locator = locator.filter(has_text=context)
            count = locator.count()
            if count != 1:
                raise BrowserActionError(
                    "AMBIGUOUS_TARGET" if count > 1 else "BROWSER_TARGET_NOT_FOUND",
                    "Context did not identify exactly one page element.",
                )
        before_url = page.url
        before_title = page.title()
        before_text = self._read_page_text(page, 2_000)
        locator.click(timeout=self.timeout_ms)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=min(self.timeout_ms, 2_000))
        except Exception as exc:
            if "Timeout" not in type(exc).__name__:
                raise
        after_title = page.title()
        after_text = self._read_page_text(page, 2_000)
        navigated = page.url != before_url
        changed = navigated or after_title != before_title or after_text != before_text
        if not changed:
            raise BrowserActionError(
                "BROWSER_VERIFICATION_FAILED",
                "The click completed, but no resulting page change could be verified.",
            )
        return {
            "success": True,
            "action": "clicked",
            "url_before": redact_url(before_url),
            "url": redact_url(page.url),
            "title": redact_sensitive_text(after_title),
            "navigation_observed": navigated,
            "page_change_verified": changed,
        }

    def _do_type_text(self, strategy: str, value: str, text: str, role: str | None) -> dict[str, Any]:
        page = self._current_page()
        locator = self._locator(page, strategy, value, role)
        locator.fill(text, timeout=self.timeout_ms)
        try:
            observed = locator.input_value(timeout=self.timeout_ms)
        except Exception as exc:
            raise BrowserActionError(
                "BROWSER_VERIFICATION_FAILED",
                "Could not verify the resulting input value.",
            ) from exc
        if observed != text:
            raise BrowserActionError("BROWSER_VERIFICATION_FAILED", "The input value did not match the requested text.")
        return {"success": True, "action": "typed", "verified": True}

    def _do_get_page_info(self) -> dict[str, Any]:
        page = self._current_page()
        return {
            "success": True,
            "url": redact_url(page.url),
            "title": redact_sensitive_text(page.title()),
        }

    def _do_get_text(self, max_chars: int) -> dict[str, Any]:
        page = self._current_page()
        try:
            body_text = self._read_page_text(page, min(max_chars, MAX_PAGE_TEXT))
        except Exception as exc:
            if "Timeout" in type(exc).__name__:
                raise BrowserActionError("BROWSER_TIMEOUT", "Visible page text did not become available in time.", retryable=True) from exc
            raise BrowserActionError("BROWSER_VERIFICATION_FAILED", "Visible page text could not be read.") from exc
        return {
            "success": True,
            "url": redact_url(page.url),
            "text": redact_sensitive_text(body_text),
            "truncated": len(body_text) > min(max_chars, MAX_PAGE_TEXT),
            "untrusted_web_content": True,
        }

    def _do_back(self) -> dict[str, Any]:
        page = self._current_page()
        response = page.go_back(wait_until="domcontentloaded", timeout=self.timeout_ms)
        if response is None:
            raise BrowserActionError("BROWSER_VERIFICATION_FAILED", "The browser had no previous page.")
        return {
            "success": True,
            "url": redact_url(page.url),
            "title": redact_sensitive_text(page.title()),
        }

    def _do_refresh(self) -> dict[str, Any]:
        page = self._current_page()
        response = page.reload(wait_until="domcontentloaded", timeout=self.timeout_ms)
        if response is not None and response.status >= 400:
            raise BrowserActionError("BROWSER_NAVIGATION_FAILED", f"The refreshed page returned HTTP {response.status}.")
        return {
            "success": True,
            "url": redact_url(page.url),
            "title": redact_sensitive_text(page.title()),
        }

    def _do_close(self) -> dict[str, Any]:
        self._close_context()
        return {"success": True, "action": "closed"}

    def _close_context(self) -> None:
        if self._context is not None:
            self._context.close()
        self._context = None
        self._browser_name = None
