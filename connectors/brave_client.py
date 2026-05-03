from __future__ import annotations

from datetime import UTC, datetime

import httpx

from services.analyze_retrieval.schemas import BraveRetrievalError
from services.market_recap.schemas import Candidate

_BRAVE_CONTEXT_URL = "https://api.search.brave.com/res/v1/llm/context"


def _parse_published_date(age_values: object) -> datetime | None:
    if not isinstance(age_values, list):
        return None
    for entry in age_values:
        if not isinstance(entry, str):
            continue
        value = entry.strip()
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if len(value) == 10:
            parsed = parsed.replace(hour=12, minute=0, second=0, microsecond=0)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return None


class BraveClient:
    def __init__(self, api_key: str, http_client: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._http_client = http_client or httpx.Client(timeout=10.0)

    def search(
        self,
        *,
        query: str,
        country: str,
        search_lang: str,
        goggle: str,
        count: int = 20,
        freshness: str | None = None,
    ) -> list[Candidate]:
        params = {
            "q": query,
            "country": country,
            "search_lang": search_lang,
            "count": count,
            "context_threshold_mode": "strict",
            "goggles": goggle,
        }
        if freshness:
            params["freshness"] = freshness
        try:
            response = self._http_client.get(
                _BRAVE_CONTEXT_URL,
                headers={"X-Subscription-Token": self._api_key},
                params=params,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BraveRetrievalError("Brave request failed") from exc

        grounding = payload.get("grounding")
        if not isinstance(grounding, dict):
            raise BraveRetrievalError("Brave response shape is invalid")
        generic_items = grounding.get("generic")
        if not isinstance(generic_items, list):
            raise BraveRetrievalError("Brave response shape is invalid")

        sources = payload.get("sources")
        sources_by_url = sources if isinstance(sources, dict) else {}
        results = payload.get("results")
        result_rows = results if isinstance(results, list) else []

        meta_by_url: dict[str, dict[str, str]] = {}
        for row in result_rows:
            if not isinstance(row, dict):
                continue
            url = row.get("url")
            if not isinstance(url, str) or not url:
                continue
            meta_by_url[url] = {
                "title": row.get("title", "") if isinstance(row.get("title"), str) else "",
                "snippet": row.get("description", "") if isinstance(row.get("description"), str) else "",
            }

        candidates: list[Candidate] = []
        for row in generic_items:
            if not isinstance(row, dict):
                continue
            url = row.get("url")
            if not isinstance(url, str) or not url:
                continue
            snippets = row.get("snippets")
            snippet_chunks = [s for s in snippets if isinstance(s, str)] if isinstance(snippets, list) else []
            source_obj = sources_by_url.get(url)
            age_values = source_obj.get("age") if isinstance(source_obj, dict) else None
            published_date = _parse_published_date(age_values)
            meta = meta_by_url.get(url, {})
            grounded_title = row.get("title") if isinstance(row.get("title"), str) else ""
            source_title = (
                source_obj.get("title")
                if isinstance(source_obj, dict) and isinstance(source_obj.get("title"), str)
                else ""
            )

            candidates.append(
                Candidate(
                    title=grounded_title or meta.get("title", "") or source_title,
                    url=url,
                    snippet=meta.get("snippet", ""),
                    published_date=published_date,
                    raw_content="\n\n".join(snippet_chunks),
                    score=0.0,
                    provider="brave",
                )
            )
        return candidates
