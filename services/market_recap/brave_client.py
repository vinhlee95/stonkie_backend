from datetime import UTC, date, datetime

import httpx

from services.market_recap.schemas import Candidate
from services.market_recap.source_policy import ALLOWLIST_BY_MARKET


def _build_vn_goggle() -> str:
    return "\n".join(f"$boost=3,site={domain}" for domain in sorted(ALLOWLIST_BY_MARKET["VN"]))


def _midpoint_datetime(period_start: date, period_end: date) -> datetime:
    midpoint_ordinal = (period_start.toordinal() + period_end.toordinal()) // 2
    midpoint = date.fromordinal(midpoint_ordinal)
    return datetime(midpoint.year, midpoint.month, midpoint.day, 12, 0, tzinfo=UTC)


def _parse_age_entry(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return datetime(parsed.year, parsed.month, parsed.day, 12, 0, tzinfo=UTC)


class BraveClient:
    def __init__(self, api_key: str, http_client: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._http_client = http_client or httpx.Client(timeout=10.0)

    def search(
        self,
        query: str,
        period_start: date,
        period_end: date,
        include_domains: list[str] | None = None,
    ) -> list[Candidate]:
        del include_domains
        response = self._http_client.get(
            "https://api.search.brave.com/res/v1/llm/context",
            headers={"X-Subscription-Token": self._api_key},
            params={
                "q": query,
                "country": "ALL",
                "search_lang": "vi",
                "count": 30,
                "freshness": f"{period_start.isoformat()}to{period_end.isoformat()}",
                "goggles": _build_vn_goggle(),
            },
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
            return candidates

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
        return candidates
