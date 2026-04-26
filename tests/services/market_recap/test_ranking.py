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


def test_rank_prefers_article_page_over_generic_hub_when_dates_match():
    same_time = datetime(2026, 4, 24, 10, 0, tzinfo=UTC)
    generic = _candidate(
        title="Financial Markets",
        url="https://apnews.com/hub/financial-markets",
        score=0.9,
        published_date=same_time,
    )
    article = _candidate(
        title="How major US stock indexes fared Friday 4/24/2026",
        url="https://apnews.com/article/wall-street-stocks-dow-nasdaq-fc22c5b3b62593817c7e0eba52a42ce1",
        score=0.1,
        published_date=same_time,
    )

    ranked = rank([generic, article])
    assert [candidate.title for candidate in ranked] == [
        "How major US stock indexes fared Friday 4/24/2026",
        "Financial Markets",
    ]


def test_rank_demotes_generic_homepages_and_video_pages():
    same_time = datetime(2026, 4, 24, 10, 0, tzinfo=UTC)
    homepage = _candidate(
        title="The New York Stock Exchange",
        url="https://www.nyse.com/index",
        score=0.9,
        published_date=same_time,
    )
    video_landing = _candidate(
        title="Wall St ends mixed as investors parse Middle East ...",
        url="https://www.reuters.com/video/markets/",
        score=0.9,
        published_date=same_time,
    )
    article = _candidate(
        title="US chipmakers hit record highs as Intel turbocharges AI rally",
        url="https://www.reuters.com/business/us-chipmakers-hit-record-highs-intel-turbocharges-ai-rally-2026-04-24/",
        score=0.1,
        published_date=same_time,
    )

    ranked = rank([homepage, video_landing, article])
    assert ranked[0].title == "US chipmakers hit record highs as Intel turbocharges AI rally"
    assert ranked[-1].title in {
        "The New York Stock Exchange",
        "Wall St ends mixed as investors parse Middle East ...",
    }


def test_rank_demotes_institutional_event_pages_and_index_summaries():
    same_time = datetime(2026, 4, 24, 10, 0, tzinfo=UTC)
    board_meeting = _candidate(
        title="Federal Reserve Board - April 28, 2026 - Closed Board Meeting",
        url="https://www.federalreserve.gov/aboutthefed/boardmeetings/20260428closed.htm",
        score=0.9,
        published_date=same_time,
    )
    index_summary = _candidate(
        title="How major US stock indexes fared Friday 4/24/2026",
        url="https://apnews.com/article/wall-street-stocks-dow-nasdaq-fc22c5b3b62593817c7e0eba52a42ce1",
        score=0.9,
        published_date=same_time,
    )
    richer_article = _candidate(
        title="US chipmakers hit record highs as Intel turbocharges AI rally",
        url="https://www.reuters.com/business/us-chipmakers-hit-record-highs-intel-turbocharges-ai-rally-2026-04-24/",
        score=0.1,
        published_date=same_time,
    )

    ranked = rank([board_meeting, index_summary, richer_article])
    assert ranked[0].title == "US chipmakers hit record highs as Intel turbocharges AI rally"
    assert ranked[-1].title == "Federal Reserve Board - April 28, 2026 - Closed Board Meeting"


def test_rank_for_fi_prefers_finnish_market_specific_coverage():
    same_time = datetime(2026, 4, 24, 10, 0, tzinfo=UTC)
    broad_europe = Candidate(
        title="Europe markets mixed as energy prices rise",
        url="https://www.bloomberg.com/news/articles/2026-04-24/europe-markets-mixed",
        snippet="Regional macro sentiment in Europe",
        published_date=same_time,
        raw_content="Broad euro-zone market coverage.",
        score=0.9,
        provider="tavily",
    )
    finland_specific = Candidate(
        title="OMX Helsinki rises as Finnish industrials lead gains",
        url="https://www.investing.com/equities/finland",
        snippet="Helsinki stock exchange weekly move",
        published_date=same_time,
        raw_content="Finnish market breadth improved on OMX Helsinki.",
        score=0.1,
        provider="tavily",
    )

    ranked = rank([broad_europe, finland_specific], market="FI")
    assert ranked[0].title == "OMX Helsinki rises as Finnish industrials lead gains"
