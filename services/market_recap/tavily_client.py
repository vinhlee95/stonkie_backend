from datetime import date, datetime
from email.utils import parsedate_to_datetime

import httpx

from services.market_recap.schemas import Candidate


class TavilyClient:
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
        payload: dict[str, str | int | bool | list[str]] = {
            "query": query,
            "search_depth": "basic",
            "topic": "news",
            "max_results": 5,
            "include_raw_content": True,
            "start_date": period_start.isoformat(),
            "end_date": period_end.isoformat(),
        }
        if include_domains:
            payload["include_domains"] = include_domains

        response = self._http_client.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json=payload,
        )
        response.raise_for_status()

        data = response.json()
        return [self._normalize_result(item) for item in data.get("results", [])]

    @staticmethod
    def _parse_published_date(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                return parsedate_to_datetime(value)
            except (TypeError, ValueError):
                return None

    @staticmethod
    def _normalize_result(item: dict) -> Candidate:
        published_value = item.get("published_date")
        published_date = TavilyClient._parse_published_date(
            published_value if isinstance(published_value, str) else None
        )

        raw_content = item.get("raw_content") or ""

        return Candidate(
            title=item.get("title", ""),
            url=item.get("url", ""),
            snippet=item.get("content", ""),
            published_date=published_date,
            raw_content=raw_content,
            score=float(item.get("score") or 0.0),
            provider="tavily",
        )
