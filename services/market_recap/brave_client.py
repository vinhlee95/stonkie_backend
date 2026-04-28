import re
from collections import Counter
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from services.market_recap.schemas import Candidate
from services.market_recap.source_policy import ALLOWLIST_BY_MARKET


def _build_goggle(*, market: str, include_domains: list[str] | None = None) -> str:
    market_key = market.upper()
    domains = sorted(set(include_domains or ALLOWLIST_BY_MARKET.get(market_key, ALLOWLIST_BY_MARKET["US"])))
    lines = [f"$boost=3,site={domain}" for domain in domains]
    lines.extend(
        [
            "$discard=reddit.com",
            "$discard=x.com",
            "$discard=twitter.com",
            "$discard=youtube.com",
        ]
    )
    return "\n".join(lines)


def _build_vn_goggle(include_domains: list[str] | None = None) -> str:
    return _build_goggle(market="VN", include_domains=include_domains)


def _country_for(market: str) -> str:
    market_key = market.upper()
    if market_key == "US":
        return "US"
    if market_key == "FI":
        return "FI"
    return "ALL"


def _search_lang_for(market: str) -> str:
    return "vi" if market.upper() == "VN" else "en"


def _midpoint_datetime(period_start: date, period_end: date) -> datetime:
    midpoint_ordinal = (period_start.toordinal() + period_end.toordinal()) // 2
    midpoint = date.fromordinal(midpoint_ordinal)
    return datetime(midpoint.year, midpoint.month, midpoint.day, 12, 0, tzinfo=UTC)


def _parse_age_entry(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", raw):
        parsed = date.fromisoformat(raw)
        return datetime(parsed.year, parsed.month, parsed.day, 12, 0, tzinfo=UTC)
    try:
        parsed_dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed_dt = None
    if parsed_dt is not None:
        if parsed_dt.tzinfo is None:
            parsed_dt = parsed_dt.replace(tzinfo=UTC)
        return parsed_dt.astimezone(UTC)
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        parsed = None
    if parsed is not None:
        return datetime(parsed.year, parsed.month, parsed.day, 12, 0, tzinfo=UTC)
    try:
        rfc_dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        rfc_dt = None
    if rfc_dt is not None:
        if rfc_dt.tzinfo is None:
            rfc_dt = rfc_dt.replace(tzinfo=UTC)
        return rfc_dt.astimezone(UTC)
    return None


def _parse_date_hint(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    match = re.search(r"(20\d{2})-(\d{2})-(\d{2})", value)
    if match is None:
        return None
    try:
        parsed = date.fromisoformat(f"{match.group(1)}-{match.group(2)}-{match.group(3)}")
    except ValueError:
        return None
    return datetime(parsed.year, parsed.month, parsed.day, 12, 0, tzinfo=UTC)


class BraveClient:
    def __init__(self, api_key: str, http_client: httpx.Client | None = None, market: str = "VN") -> None:
        self._api_key = api_key
        self._http_client = http_client or httpx.Client(timeout=10.0)
        self._market = market.upper()

    def search(
        self,
        query: str,
        period_start: date,
        period_end: date,
        include_domains: list[str] | None = None,
    ) -> list[Candidate]:
        candidates, _ = self.search_with_snapshot(
            query=query,
            period_start=period_start,
            period_end=period_end,
            include_domains=include_domains,
        )
        return candidates

    def search_with_snapshot(
        self,
        query: str,
        period_start: date,
        period_end: date,
        include_domains: list[str] | None = None,
    ) -> tuple[list[Candidate], dict]:
        params = {
            "q": query,
            "country": _country_for(self._market),
            "search_lang": _search_lang_for(self._market),
            "count": 30,
            "context_threshold_mode": "strict",
            "freshness": f"{period_start.isoformat()}to{period_end.isoformat()}",
            "goggles": _build_goggle(market=self._market, include_domains=include_domains),
        }
        response = self._http_client.get(
            "https://api.search.brave.com/res/v1/llm/context",
            headers={"X-Subscription-Token": self._api_key},
            params=params,
        )
        response.raise_for_status()
        data = response.json()

        meta_by_url: dict[str, dict[str, str]] = {}
        for item in data.get("results", []):
            url = item.get("url")
            if isinstance(url, str):
                meta_by_url[url] = {
                    "title": item.get("title", ""),
                    "snippet": item.get("description", ""),
                }

        sources = data.get("sources", {})
        midpoint = _midpoint_datetime(period_start, period_end)
        candidates: list[Candidate] = []
        grounded_items = data.get("grounding", {}).get("generic", [])
        for item in grounded_items:
            url = item.get("url", "")
            if not isinstance(url, str) or not url:
                continue

            age_values = sources.get(url, {}).get("age", []) if isinstance(sources, dict) else []
            published_date = None
            if isinstance(age_values, list):
                for entry in age_values:
                    published_date = _parse_age_entry(entry)
                    if published_date is not None:
                        break
            if published_date is None:
                published_date = _parse_date_hint(url)
            if published_date is None:
                published_date = _parse_date_hint(item.get("title", ""))
            if published_date is None:
                published_date = midpoint

            snippets_raw = item.get("snippets", [])
            snippets = (
                [snippet for snippet in snippets_raw if isinstance(snippet, str)]
                if isinstance(snippets_raw, list)
                else []
            )
            meta = meta_by_url.get(url, {})
            candidates.append(
                Candidate(
                    title=item.get("title", "") or meta.get("title", ""),
                    url=url,
                    snippet=meta.get("snippet", ""),
                    published_date=published_date,
                    raw_content="\n\n".join(snippets),
                    score=0.0,
                    provider="brave",
                )
            )
        if candidates:
            return candidates, {
                "provider": "brave",
                "query": query,
                "market": self._market,
                "include_domains": include_domains or [],
                "request_params": params,
                "response_http_status": response.status_code,
                "response_items": len(candidates),
                "shape": "grounding.generic",
                "response_summary": _safe_response_summary(data),
            }

        # Fallback shape: some responses may provide only `results`.
        for result in data.get("results", []):
            url = result.get("url", "")
            if not isinstance(url, str) or not url:
                continue
            age_values = sources.get(url, {}).get("age", []) if isinstance(sources, dict) else []
            published_date = None
            if isinstance(age_values, list):
                for entry in age_values:
                    published_date = _parse_age_entry(entry)
                    if published_date is not None:
                        break
            if published_date is None:
                published_date = _parse_date_hint(url)
            if published_date is None:
                published_date = _parse_date_hint(result.get("title", ""))
            if published_date is None:
                published_date = midpoint
            candidates.append(
                Candidate(
                    title=result.get("title", ""),
                    url=url,
                    snippet=result.get("description", ""),
                    published_date=published_date,
                    raw_content="",
                    score=0.0,
                    provider="brave",
                )
            )
        return candidates, {
            "provider": "brave",
            "query": query,
            "market": self._market,
            "include_domains": include_domains or [],
            "request_params": params,
            "response_http_status": response.status_code,
            "response_items": len(candidates),
            "shape": "results",
            "response_summary": _safe_response_summary(data),
        }


def _safe_response_summary(data: dict) -> dict:
    results = data.get("results", [])
    sources = data.get("sources", {})
    grounding_items = data.get("grounding", {}).get("generic", [])

    urls: list[str] = []
    if isinstance(grounding_items, list):
        for item in grounding_items:
            url = item.get("url") if isinstance(item, dict) else None
            if isinstance(url, str) and url:
                urls.append(url)
    if not urls and isinstance(results, list):
        for item in results:
            url = item.get("url") if isinstance(item, dict) else None
            if isinstance(url, str) and url:
                urls.append(url)

    domain_counts = Counter((urlsplit(url).hostname or "").lower() for url in urls if url)
    return {
        "results_count": len(results) if isinstance(results, list) else 0,
        "grounding_count": len(grounding_items) if isinstance(grounding_items, list) else 0,
        "sources_count": len(sources) if isinstance(sources, dict) else 0,
        "domain_counts": dict(domain_counts),
    }
