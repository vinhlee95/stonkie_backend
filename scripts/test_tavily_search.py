"""Manual smoke test for Tavily /search — not a pytest.

Usage:
    source venv/bin/activate
    PYTHONPATH=. python scripts/test_tavily_search.py
    PYTHONPATH=. python scripts/test_tavily_search.py --query "Fed rate decision"

TAVILY_API_KEY is loaded from .env. Full JSON response is always written to
scripts/tavily_response.json (or the path passed via --out) for inspection.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

TAVILY_URL = "https://api.tavily.com/search"

DEFAULT_ALLOWLIST = [
    "reuters.com",
    "wsj.com",
    "bloomberg.com",
    "ft.com",
    "cnbc.com",
    "marketwatch.com",
    "barrons.com",
    "apnews.com",
]


def prior_business_week() -> tuple[date, date]:
    today = date.today()
    last_friday = today - timedelta(days=(today.weekday() - 4) % 7 or 7)
    last_monday = last_friday - timedelta(days=4)
    return last_monday, last_friday


def run(query: str, start: date, end: date, allowlist: list[str] | None) -> dict:
    load_dotenv()
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        sys.exit("TAVILY_API_KEY not set (looked in env and .env)")

    payload = {
        "api_key": api_key,
        "query": query,
        "topic": "news",
        "search_depth": "advanced",
        "max_results": 10,
        "include_answer": False,
        "include_raw_content": True,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    if allowlist:
        payload["include_domains"] = allowlist

    resp = requests.post(TAVILY_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def summarize(data: dict) -> None:
    results = data.get("results", [])
    print(f"\n=== {len(results)} results in {data.get('response_time')}s ===\n")
    for i, r in enumerate(results):
        raw = r.get("raw_content") or ""
        print(f"[{i}] {r.get('title')}")
        print(f"    url:            {r.get('url')}")
        print(f"    published_date: {r.get('published_date')}")
        print(f"    score:          {r.get('score')}")
        print(f"    snippet:        {(r.get('content') or '')[:120]}...")
        print(f"    raw_content:    {len(raw)} chars" + (" (MISSING)" if not raw else ""))
        print()

    extracted = sum(1 for r in results if r.get("raw_content"))
    print(f"extraction hit rate: {extracted}/{len(results)}")


def main() -> None:
    last_monday, last_friday = prior_business_week()

    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="US stock market weekly recap")
    parser.add_argument("--start", default=last_monday.isoformat())
    parser.add_argument("--end", default=last_friday.isoformat())
    parser.add_argument("--no-allowlist", action="store_true")
    parser.add_argument(
        "--out",
        default=str(Path(__file__).parent / "tavily_response.json"),
        help="Path to write full JSON response (default: scripts/tavily_response.json)",
    )
    args = parser.parse_args()

    allowlist = None if args.no_allowlist else DEFAULT_ALLOWLIST
    data = run(
        query=args.query,
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        allowlist=allowlist,
    )

    out_path = Path(args.out)
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    summarize(data)
    print(f"\nfull response written to: {out_path}")


if __name__ == "__main__":
    main()
