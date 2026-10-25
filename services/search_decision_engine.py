"""Web-search decision type shared by the v2 analyze routers and handlers, plus the static-fact pre-filter."""

import re
from dataclasses import dataclass

# Patterns for historical/static company facts that never need search
_STATIC_FACT_PATTERNS = [
    re.compile(r"\b(who|whom)\b.*(found|establish|start|creat|incorporat)", re.IGNORECASE),
    re.compile(r"\bwhen\b.*(found|establish|start|creat|incorporat)", re.IGNORECASE),
    re.compile(r"\b(where)\b.*(headquarter|based|located|hq)", re.IGNORECASE),
    re.compile(r"\b(what|where|when)\b.*(ipo date|went public|go public|listed)", re.IGNORECASE),
    re.compile(r"\bnamed after\b|\bname (come|origin|meaning|etymol)", re.IGNORECASE),
    re.compile(r"\boriginal(ly)? (name|called)\b", re.IGNORECASE),
]


def is_static_fact(question: str) -> bool:
    """Check if question matches known historical/static fact patterns."""
    return any(p.search(question) for p in _STATIC_FACT_PATTERNS)


@dataclass(frozen=True)
class SearchDecision:
    use_google_search: bool
    reason_code: str
    confidence: float
    decision_model: str
    decision_fallback: str  # "none" | "router_fail_safe_on" (default routing after a router failure)
