from datetime import UTC, datetime

from services.market_recap.schemas import Candidate
from services.ticker_recap.ranking import rank, ticker_relevance_rank


def _candidate(*, title: str, url: str, snippet: str = "", raw_content: str = "content") -> Candidate:
    return Candidate(
        title=title,
        url=url,
        snippet=snippet,
        published_date=datetime(2026, 6, 18, 12, 0, tzinfo=UTC),
        raw_content=raw_content,
        score=0.5,
        provider="brave",
    )


def test_ticker_relevance_rank_zero_when_ticker_mentioned():
    candidate = _candidate(
        title="AAPL jumps after earnings",
        url="https://www.reuters.com/a",
    )
    assert ticker_relevance_rank(candidate, ticker="AAPL", company_name="Apple Inc.") == 0


def test_ticker_relevance_rank_zero_when_company_name_mentioned():
    candidate = _candidate(
        title="Apple unveils new product line",
        url="https://www.reuters.com/a",
    )
    assert ticker_relevance_rank(candidate, ticker="AAPL", company_name="Apple Inc.") == 0


def test_ticker_relevance_rank_penalizes_off_topic_candidate():
    candidate = _candidate(
        title="Tesla recalls vehicles amid probe",
        url="https://www.reuters.com/b",
    )
    assert ticker_relevance_rank(candidate, ticker="AAPL", company_name="Apple Inc.") > 0


def test_rank_puts_on_topic_above_off_topic_filler():
    on_topic = _candidate(
        title="Apple stock climbs on iPhone demand",
        url="https://www.reuters.com/technology/apple",
        snippet="AAPL shares rose",
    )
    off_topic = _candidate(
        title="Broad market wrap: Dow and Nasdaq mixed",
        url="https://www.reuters.com/markets/wrap",
        snippet="indexes were mixed",
    )

    ranked = rank([off_topic, on_topic], ticker="AAPL", company_name="Apple Inc.")

    assert ranked[0].title == "Apple stock climbs on iPhone demand"
    assert ranked[-1].title == "Broad market wrap: Dow and Nasdaq mixed"
