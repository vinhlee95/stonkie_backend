"""Manual comparison: Brave LLM-Context API vs Tavily — not a pytest.

Compares retrieval quality on three axes:
  1. allowlist hit rate (sources match market allowlist)
  2. freshness (results inside the requested window)
  3. relevancy (count returned + provider score, plus on-screen titles for human review)

Usage:
    source venv/bin/activate
    PYTHONPATH=. python scripts/test_brave_vs_tavily.py
    PYTHONPATH=. python scripts/test_brave_vs_tavily.py --market VN \
        --query "thị trường chứng khoán Việt Nam tuần qua"
    PYTHONPATH=. python scripts/test_brave_vs_tavily.py --market US \
        --query "US stock market weekly recap"

Required env (loaded from .env):
    TAVILY_API_KEY
    BRAVE_API_KEY        (X-Subscription-Token for Brave)

Brave docs: https://api-dashboard.search.brave.com/documentation/services/llm-context
Brave does NOT expose include_domains; allowlist is enforced post-hoc here.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

from services.market_recap.source_policy import ALLOWLIST_BY_MARKET, registrable_domain
from services.market_recap.tavily_client import TavilyClient


@dataclass
class NormalizedHit:
    title: str
    url: str
    snippet: str
    published_date: datetime | None
    score: float | None
    provider: str

    def to_json(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "published_date": self.published_date.isoformat() if self.published_date else None,
            "score": self.score,
            "provider": self.provider,
        }


def parse_brave_age(age: str | None, now: datetime) -> datetime | None:
    """Brave returns `age` as a relative string ("2 days ago") or ISO date.
    Best-effort parse; returns None on failure."""
    if not age:
        return None
    age = age.strip()
    try:
        return datetime.fromisoformat(age.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(age)
    except (TypeError, ValueError):
        pass
    m = re.match(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", age, re.I)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        delta = {
            "minute": timedelta(minutes=n),
            "hour": timedelta(hours=n),
            "day": timedelta(days=n),
            "week": timedelta(weeks=n),
            "month": timedelta(days=30 * n),
            "year": timedelta(days=365 * n),
        }[unit]
        return now - delta
    return None


class BraveClient:
    """Minimal Brave LLM-Context client for this comparison script."""

    URL = "https://api.search.brave.com/res/v1/llm/context"

    def __init__(self, api_key: str, http_client: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._http = http_client or httpx.Client(timeout=15.0)

    def search(
        self,
        query: str,
        period_start: date,
        period_end: date,
        country: str = "us",
        search_lang: str = "en",
        count: int = 20,
    ) -> tuple[list[NormalizedHit], dict]:
        freshness = f"{period_start.isoformat()}to{period_end.isoformat()}"
        params = {
            "q": query,
            "country": country,
            "search_lang": search_lang,
            "count": count,
            "freshness": freshness,
        }
        resp = self._http.get(
            self.URL,
            headers={"X-Subscription-Token": self._api_key, "Accept": "application/json"},
            params=params,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Brave {resp.status_code}: {resp.text}")
        data = resp.json()

        now = datetime.now(timezone.utc)
        hits: list[NormalizedHit] = []
        sources_meta = data.get("sources") or {}
        for item in (data.get("grounding") or {}).get("generic", []) or []:
            url = item.get("url", "")
            snippets = item.get("snippets") or []
            meta = sources_meta.get(url) or {}
            age_list = meta.get("age") or []
            if not isinstance(age_list, list):
                age_list = [age_list]
            published = None
            for candidate in age_list:
                published = parse_brave_age(candidate, now)
                if published is not None:
                    break
            hits.append(
                NormalizedHit(
                    title=item.get("title", ""),
                    url=url,
                    snippet=" ".join(snippets)[:500],
                    published_date=published,
                    score=None,
                    provider="brave",
                )
            )
        return hits, data


def run_tavily(query: str, start: date, end: date, allowlist: list[str] | None) -> list[NormalizedHit]:
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        sys.exit("TAVILY_API_KEY not set")
    client = TavilyClient(api_key=api_key)
    cands = client.search(
        query=query,
        period_start=start,
        period_end=end,
        include_domains=allowlist,
    )
    return [
        NormalizedHit(
            title=c.title,
            url=c.url,
            snippet=c.snippet,
            published_date=c.published_date,
            score=c.score,
            provider="tavily",
        )
        for c in cands
    ]


def run_brave(query: str, start: date, end: date, market: str) -> tuple[list[NormalizedHit], dict]:
    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        sys.exit("BRAVE_API_KEY not set")
    # Brave does not list VN as a supported country code; fall back to ALL.
    country = "ALL" if market == "VN" else "US"
    lang = "vi" if market == "VN" else "en"
    client = BraveClient(api_key=api_key)
    return client.search(query, start, end, country=country, search_lang=lang)


def in_window(dt: datetime | None, start: date, end: date) -> bool:
    if dt is None:
        return False
    d = dt.date()
    return start <= d <= end


def evaluate(hits: list[NormalizedHit], allowlist: set[str], start: date, end: date) -> dict:
    n = len(hits)
    in_allow = sum(1 for h in hits if registrable_domain(h.url) in allowlist)
    fresh_known = [h for h in hits if h.published_date is not None]
    fresh_in = sum(1 for h in fresh_known if in_window(h.published_date, start, end))
    return {
        "count": n,
        "allowlisted": in_allow,
        "allowlist_rate": (in_allow / n) if n else 0.0,
        "with_date": len(fresh_known),
        "fresh_in_window": fresh_in,
        "fresh_in_window_rate": (fresh_in / n) if n else 0.0,
    }


def print_section(name: str, hits: list[NormalizedHit], allowlist: set[str]) -> None:
    print(f"\n=== {name} — {len(hits)} hits ===")
    for i, h in enumerate(hits):
        domain = registrable_domain(h.url)
        mark = "✓" if domain in allowlist else "✗"
        date_s = h.published_date.date().isoformat() if h.published_date else "—"
        print(f"  [{i:>2}] {mark} {domain:<25} {date_s}  {h.title[:80]}")


def main() -> None:
    today = date.today()
    last_friday = today - timedelta(days=(today.weekday() - 4) % 7 or 7)
    last_monday = last_friday - timedelta(days=4)

    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=["US", "VN"], default="VN")
    parser.add_argument("--query", default=None)
    parser.add_argument("--start", default=last_monday.isoformat())
    parser.add_argument("--end", default=last_friday.isoformat())
    parser.add_argument(
        "--out",
        default=str(Path(__file__).parent / "brave_vs_tavily_response.json"),
    )
    parser.add_argument(
        "--no-allowlist-tavily",
        action="store_true",
        help="Don't pass include_domains to Tavily (compare unconstrained)",
    )
    args = parser.parse_args()

    load_dotenv()

    default_query = (
        "thị trường chứng khoán Việt Nam tuần qua" if args.market == "VN" else "US stock market weekly recap"
    )
    query = args.query or default_query

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    allowlist = ALLOWLIST_BY_MARKET[args.market]

    print(f"market={args.market}  window={start}..{end}  query={query!r}")
    print(f"allowlist ({len(allowlist)}): {sorted(allowlist)}")

    tavily_hits = run_tavily(
        query=query,
        start=start,
        end=end,
        allowlist=None if args.no_allowlist_tavily else sorted(allowlist),
    )
    brave_hits, brave_raw = run_brave(query, start, end, args.market)

    print_section("Tavily", tavily_hits, allowlist)
    print_section("Brave", brave_hits, allowlist)

    tavily_eval = evaluate(tavily_hits, allowlist, start, end)
    brave_eval = evaluate(brave_hits, allowlist, start, end)

    print("\n=== Comparison ===")
    fmt = "  {:<22} {:>10} {:>10}"
    print(fmt.format("metric", "tavily", "brave"))
    print(fmt.format("count", tavily_eval["count"], brave_eval["count"]))
    print(
        fmt.format(
            "allowlisted",
            f"{tavily_eval['allowlisted']} ({tavily_eval['allowlist_rate']:.0%})",
            f"{brave_eval['allowlisted']} ({brave_eval['allowlist_rate']:.0%})",
        )
    )
    print(fmt.format("with_date", tavily_eval["with_date"], brave_eval["with_date"]))
    print(
        fmt.format(
            "fresh_in_window",
            f"{tavily_eval['fresh_in_window']} ({tavily_eval['fresh_in_window_rate']:.0%})",
            f"{brave_eval['fresh_in_window']} ({brave_eval['fresh_in_window_rate']:.0%})",
        )
    )

    out = {
        "params": {
            "market": args.market,
            "query": query,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "tavily_include_domains": None if args.no_allowlist_tavily else sorted(allowlist),
        },
        "tavily": {"eval": tavily_eval, "hits": [h.to_json() for h in tavily_hits]},
        "brave": {
            "eval": brave_eval,
            "hits": [h.to_json() for h in brave_hits],
            "raw": brave_raw,
        },
    }
    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nfull JSON written to: {args.out}")


if __name__ == "__main__":
    main()
