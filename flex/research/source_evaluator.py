from __future__ import annotations

from flex.research.search_provider import SearchResult, clean_query_terms

_EDUCATION_DOMAINS = (
    ".edu",
    "khanacademy.org",
    "openstax.org",
    "britannica.com",
    "physicsclassroom.com",
    "bbc.co.uk",
    "nationalgeographic.org",
)


def evaluate_source(query: str, result: SearchResult, category: str) -> dict[str, object]:
    terms = set(clean_query_terms(query))
    text = f"{result.title} {result.snippet} {result.domain}".casefold()
    matched = [term for term in terms if len(term) > 2 and term in text]
    relevance = min(1.0, len(matched) / max(1, min(len([t for t in terms if len(t) > 2]), 6)))
    educational_signal = any(domain in result.domain.casefold() for domain in _EDUCATION_DOMAINS)
    if category == "education":
        quality = 0.18 if educational_signal else 0.0
        quality += 0.08 if any(word in text for word in ("guide", "lesson", "example", "practice", "notes")) else 0.0
    else:
        quality = 0.0
    score = round(min(1.0, relevance * 0.82 + quality), 3)
    return {
        "title": result.title,
        "snippet": result.snippet,
        "url": result.url,
        "domain": result.domain,
        "relevance_score": score,
        "matched_terms": matched,
        "educational_domain_signal": educational_signal,
        "evaluation_note": (
            "A relevance heuristic based on search metadata; not an independent fact-check."
        ),
    }
