"""Manual smoke test for market recap generator (not a pytest).

Usage:
    source venv/bin/activate
    PYTHONPATH=. python scripts/test_recap_generator.py
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from services.market_recap.recap_generator import GeneratorError, generate_recap
from services.market_recap.schemas import Candidate, RetrievalResult, RetrievalStats


class FakeAgent:
    def __init__(self, chunks: list[str | dict], model_name: str = "fake/openrouter-model") -> None:
        self._chunks = chunks
        self.model_name = model_name
        self.calls: list[dict] = []

    def generate_content(self, prompt: str, use_google_search: bool = False):
        self.calls.append({"prompt": prompt, "use_google_search": use_google_search})
        return self._chunks


def _candidate(*, title: str, url: str, raw_content: str, published_date: datetime) -> Candidate:
    return Candidate(
        title=title,
        url=url,
        snippet="snippet",
        published_date=published_date,
        raw_content=raw_content,
        score=0.8,
        provider="tavily",
    )


def _retrieval() -> RetrievalResult:
    return RetrievalResult(
        candidates=[
            _candidate(
                title="Reuters equities weekly",
                url="https://www.reuters.com/world/us/us-stocks-weekly-wrap-2026-04-24/",
                raw_content="US stocks closed mixed this week as earnings season accelerated.",
                published_date=datetime(2026, 4, 24, 16, 30, tzinfo=UTC),
            ),
            _candidate(
                title="AP market movers",
                url="https://apnews.com/article/markets-movers-2026",
                raw_content="Large-cap technology names outperformed broad indexes.",
                published_date=datetime(2026, 4, 23, 13, 0, tzinfo=UTC),
            ),
        ],
        stats=RetrievalStats(
            queries_total=2,
            results_total=2,
            deduped=2,
            with_raw_content=2,
            allowlisted=2,
            ranked_top_k=2,
        ),
    )


def main() -> None:
    retrieval = _retrieval()
    period_start = date(2026, 4, 20)
    period_end = date(2026, 4, 24)
    agent = FakeAgent(
        chunks=[
            "Preface from model ",
            {"type": "url_citation", "url": "https://www.reuters.com"},
            (
                '[RECAP_JSON]{"summary":"US equities were mixed this week amid earnings and macro signals.",'
                '"bullets":[{"text":"Tech outperformed while index breadth narrowed.","source_indices":[0,1]},'
                '{"text":"Risk sentiment stayed sensitive to policy commentary.","source_indices":[0]}]}[/RECAP_JSON]'
            ),
        ]
    )

    try:
        result = generate_recap(
            retrieval,
            period_start=period_start,
            period_end=period_end,
            agent=agent,
        )
    except GeneratorError as exc:
        print(f"GeneratorError: {exc}")
        raise SystemExit(1) from exc

    print("=== Recap generator smoke test ===")
    print(f"model: {result.model}")
    print(f"offline search flag: {agent.calls[0]['use_google_search']}")
    print(f"summary: {result.payload.summary}")
    print(f"bullets: {len(result.payload.bullets)}")
    print(f"sources: {len(result.payload.sources)}")
    print()
    print("payload JSON:")
    print(json.dumps(result.payload.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()
