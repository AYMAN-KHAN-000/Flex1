from __future__ import annotations

import requests

from flex.research.research_engine import ResearchEngine
from flex.research.research_history import ResearchHistory
from flex.research.research_tools import create_research_tools
from flex.research.search_provider import SearchProvider, SearchResult


class FakeSearchProvider:
    def __init__(self, results):
        self.results = results
        self.queries = []

    def search(self, query, *, limit=8):
        self.queries.append(query)
        return self.results[:limit]


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code
        self.request_args = None

    def get(self, url, **kwargs):
        self.request_args = (url, kwargs)
        return FakeResponse(self.text, self.status_code)


def test_search_provider_extracts_deduplicated_external_results():
    html = """
    <div class="dd algo algo-sr">
      <div class="compTitle">
        <a data-matarget="algo"
           href="https://r.search.yahoo.com/_ylt=abc/RU=https%3a%2f%2fexample.org%2flesson/RK=2/RS=x">
          <h3><span>Speed and velocity</span></h3>
        </a>
      </div>
      <div class="compText"><p>Clear examples and practice questions.</p></div>
    </div>
    <div class="dd algo algo-sr">
      <div class="compTitle">
        <a data-matarget="algo" href="https://r.search.yahoo.com/_ylt=abc/RU=https%3a%2f%2fexample.org%2flesson/RK=2/RS=x">
          <h3><span>Duplicate page</span></h3>
        </a>
      </div>
    </div>
    """
    session = FakeSession(html)
    provider = SearchProvider(session=session)

    results = provider.search("speed velocity")

    assert len(results) == 1
    assert results[0].title == "Speed and velocity"
    assert results[0].snippet == "Clear examples and practice questions."
    assert results[0].domain == "example.org"
    assert results[0].url == "https://example.org/lesson"
    assert session.request_args[0] == "https://search.yahoo.com/search"
    assert session.request_args[1]["params"] == {"p": "speed velocity"}


def test_search_provider_rejects_challenge_pages_as_search_failures():
    provider = SearchProvider(
        session=FakeSession("<html>challenge anomaly.js</html>", status_code=202)
    )

    try:
        provider.search("gaming keyboard Bangladesh")
    except requests.RequestException as exc:
        assert "challenge page" in str(exc)
    else:
        raise AssertionError("Search challenge pages must not look like empty results.")


def test_research_runs_multiple_queries_and_records_selected_sources(tmp_path):
    results = [
        SearchResult(
            "Speed and velocity guide",
            "Physics notes with clear explanations and examples.",
            "https://physicsclassroom.com/velocity",
            "physicsclassroom.com",
        ),
        SearchResult(
            "An unrelated page",
            "A general collection.",
            "https://example.org/other",
            "example.org",
        ),
    ]
    provider = FakeSearchProvider(results)
    history = ResearchHistory(tmp_path / "flex.db")
    engine = ResearchEngine(provider, history)

    result = engine.research("physics speed and velocity", category="education")

    assert len(provider.queries) == 2
    assert result["source_count"] == 2
    assert result["recommended_result"]["url"] == "https://physicsclassroom.com/velocity"
    assert result["untrusted_web_data"] is True
    saved = history.get(result["research_id"])
    assert saved["request"] == "physics speed and velocity"
    assert saved["selected_result"]["url"] == result["recommended_result"]["url"]


def test_product_research_never_selects_without_in_budget_price_evidence(tmp_path):
    results = [
        SearchResult(
            "Great keyboard",
            "Good quality and many reviews.",
            "https://shop.example/keyboard",
            "shop.example",
        ),
        SearchResult(
            "Keyboard ৳1,750",
            "USB keyboard, warranty included.",
            "https://store.example/item",
            "store.example",
        ),
    ]
    engine = ResearchEngine(
        FakeSearchProvider(results),
        ResearchHistory(tmp_path / "flex.db"),
    )

    result = engine.research(
        "keyboard",
        category="product",
        budget_amount=2000,
        budget_currency="BDT",
        market="Bangladesh",
    )

    assert result["recommended_result"]["url"] == "https://store.example/item"
    assert result["price_evidence"]["https://store.example/item"] == 1750

    no_evidence = engine.research(
        "keyboard",
        category="product",
        budget_amount=1000,
        budget_currency="BDT",
    )
    assert no_evidence["recommended_result"] is None


def test_research_handles_provider_failure_and_selected_page_open(tmp_path, monkeypatch):
    provider = FakeSearchProvider([])

    def failed_search(_query, *, limit):
        raise requests.ConnectionError("offline")

    provider.search = failed_search
    history = ResearchHistory(tmp_path / "flex.db")
    tools = {
        tool.name: tool
        for tool in create_research_tools(
            ResearchEngine(provider, history),
            history,
        )
    }

    failure = tools["research_web"].executor(query="test research")
    assert failure["success"] is False
    assert failure["error"]["code"] == "RESEARCH_FAILED"

    record = history.save(
        "example",
        "general",
        [{"title": "Example", "url": "https://example.org"}],
        "test",
        selected_result={"title": "Example", "url": "https://example.org"},
    )
    opened_browsers = []
    monkeypatch.setattr(
        "flex.research.research_tools.open_website_executor",
        lambda website, browser: (
            opened_browsers.append(browser)
            or {
                "status": "opened",
                "website": website,
                "url": website,
                "browser": browser,
            }
        ),
    )
    opened = tools["open_recommended_page"].executor(
        research_id=record["id"],
        browser="chrome",
    )
    assert opened["success"] is True
    assert opened_browsers == ["chrome"]
    assert history.get(record["id"])["opened"] is True
