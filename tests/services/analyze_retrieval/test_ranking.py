from datetime import UTC, datetime, timedelta

from services.analyze_retrieval.ranking import rank_for_chat
from services.market_recap.schemas import Candidate


def _candidate(
    *,
    url: str,
    published_date: datetime | None,
    raw_content: str,
    score: float,
) -> Candidate:
    return Candidate(
        title=url,
        url=url,
        snippet="",
        published_date=published_date,
        raw_content=raw_content,
        score=score,
        provider="brave",
    )


def test_rank_for_chat_orders_by_tier_recency_length_score_and_url() -> None:
    now = datetime.now(UTC)
    candidates = [
        _candidate(
            url="https://random.example.com/a",
            published_date=now - timedelta(days=5),
            raw_content="x" * 50,
            score=999.0,
        ),
        _candidate(
            url="https://www.reuters.com/world/article-1",
            published_date=now - timedelta(days=10),
            raw_content="x" * 10,
            score=1.0,
        ),
        _candidate(
            url="https://www.reuters.com/world/article-2",
            published_date=now - timedelta(days=200),
            raw_content="x" * 500,
            score=100.0,
        ),
        _candidate(
            url="https://investing.com/news/b",
            published_date=now - timedelta(days=2),
            raw_content="x" * 100,
            score=20.0,
        ),
    ]

    ranked = rank_for_chat(candidates, market="GLOBAL")
    assert [item.url for item in ranked] == [
        "https://www.reuters.com/world/article-1",
        "https://www.reuters.com/world/article-2",
        "https://investing.com/news/b",
        "https://random.example.com/a",
    ]


def test_rank_for_chat_treats_missing_published_date_as_old() -> None:
    now = datetime.now(UTC)
    candidates = [
        _candidate(
            url="https://www.reuters.com/world/article-1",
            published_date=None,
            raw_content="x" * 100,
            score=99.0,
        ),
        _candidate(
            url="https://www.reuters.com/world/article-2",
            published_date=now - timedelta(days=5),
            raw_content="x" * 10,
            score=1.0,
        ),
    ]

    ranked = rank_for_chat(candidates, market="GLOBAL")
    assert [item.url for item in ranked] == [
        "https://www.reuters.com/world/article-2",
        "https://www.reuters.com/world/article-1",
    ]


def test_rank_for_chat_returns_empty_list_for_empty_input() -> None:
    assert rank_for_chat([], market="GLOBAL") == []


def test_rank_for_chat_uses_url_as_final_tiebreaker() -> None:
    now = datetime.now(UTC)
    candidates = [
        _candidate(
            url="https://example.com/b",
            published_date=now - timedelta(days=1),
            raw_content="abc",
            score=1.0,
        ),
        _candidate(
            url="https://example.com/a",
            published_date=now - timedelta(days=1),
            raw_content="abc",
            score=1.0,
        ),
    ]

    ranked = rank_for_chat(candidates, market="GLOBAL")
    assert [item.url for item in ranked] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
