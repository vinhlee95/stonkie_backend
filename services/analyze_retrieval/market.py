from __future__ import annotations

from services.analyze_retrieval.source_policy import Market

_COUNTRY_TO_MARKET: dict[str, Market] = {
    "us": "GLOBAL",
    "usa": "GLOBAL",
    "united states": "GLOBAL",
    "united states of america": "GLOBAL",
    "vn": "VN",
    "vietnam": "VN",
    "viet nam": "VN",
    "fi": "FI",
    "finland": "FI",
}


def resolve_market(country: str | None, question_text: str) -> Market:
    _ = question_text
    if country:
        key = country.strip().lower()
        if key in _COUNTRY_TO_MARKET:
            return _COUNTRY_TO_MARKET[key]
    return "GLOBAL"
