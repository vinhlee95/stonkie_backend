from datetime import UTC, datetime

from services.market_recap.ranking import dedupe, rank
from services.market_recap.schemas import Candidate


def _candidate(
    *,
    url: str,
    score: float,
    published_date: datetime,
    title: str,
) -> Candidate:
    return Candidate(
        title=title,
        url=url,
        snippet="",
        published_date=published_date,
        raw_content="content",
        score=score,
        provider="tavily",
    )


def test_dedupe_keeps_higher_score_for_same_source_id():
    low = _candidate(
        title="Low score",
        url="https://example.com/article?utm_source=newsletter",
        score=0.2,
        published_date=datetime(2026, 4, 24, 12, 0, tzinfo=UTC),
    )
    high = _candidate(
        title="High score",
        url="https://example.com/article",
        score=0.9,
        published_date=datetime(2026, 4, 24, 13, 0, tzinfo=UTC),
    )

    output = dedupe([low, high])

    assert len(output) == 1
    assert output[0].title == "High score"


def test_rank_is_lexicographic_and_deterministic():
    same_time = datetime(2026, 4, 24, 10, 0, tzinfo=UTC)
    older_time = datetime(2026, 4, 23, 10, 0, tzinfo=UTC)
    candidates = [
        _candidate(
            title="Non-allowlisted newer",
            url="https://example.com/newer",
            score=0.95,
            published_date=same_time,
        ),
        _candidate(
            title="Allowlisted older",
            url="https://www.reuters.com/markets/older",
            score=0.20,
            published_date=older_time,
        ),
        _candidate(
            title="Allowlisted newer low score",
            url="https://www.reuters.com/markets/newer-low",
            score=0.10,
            published_date=same_time,
        ),
        _candidate(
            title="Allowlisted newer high score",
            url="https://www.reuters.com/markets/newer-high",
            score=0.90,
            published_date=same_time,
        ),
    ]

    ranked_a = rank(candidates)
    ranked_b = rank(list(reversed(candidates)))

    assert [candidate.title for candidate in ranked_a] == [
        "Allowlisted newer high score",
        "Allowlisted newer low score",
        "Allowlisted older",
        "Non-allowlisted newer",
    ]
    assert [candidate.title for candidate in ranked_a] == [candidate.title for candidate in ranked_b]
