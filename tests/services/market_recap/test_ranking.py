from datetime import UTC, date, datetime

from services.market_recap.ranking import _quality_rank, dedupe, rank, select_candidates
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


def _market_candidate(
    *,
    url: str,
    title: str = "Stocks close higher",
    published_date: datetime = datetime(2026, 11, 3, 12, 0, tzinfo=UTC),
    raw_content: str = "x" * 900,
) -> Candidate:
    return Candidate(
        title=title,
        url=url,
        snippet="",
        published_date=published_date,
        raw_content=raw_content,
        score=0.0,
        provider="brave",
    )


WRAP_TEXT = "The S&P 500 rose 1% and the Dow gained 200 points while the Nasdaq climbed. " * 12


def _select(candidates: list[Candidate], *, market: str = "US", top_k: int = 5) -> list[Candidate]:
    return select_candidates(
        candidates,
        market=market,
        period_start=date(2026, 11, 2),
        period_end=date(2026, 11, 6),
        top_k=top_k,
    )


def test_rank_gives_no_edge_to_article_url_shape():
    same_time = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    ap = _market_candidate(url="https://apnews.com/article/stocks-abc123", published_date=same_time)
    cnbc = _market_candidate(url="https://www.cnbc.com/2026/11/03/stocks.html", published_date=same_time)

    # Equal topic, length and date: the canonical URL tie-break decides, not the publisher's URL format.
    assert [c.url for c in rank([cnbc, ap])] == [ap.url, cnbc.url]
    assert [c.url for c in rank([ap, cnbc])] == [ap.url, cnbc.url]
    assert _quality_rank(ap, reward_article_paths=False) == _quality_rank(cnbc, reward_article_paths=False)


def test_quality_rank_keeps_article_url_bonus_by_default_for_ticker_recap():
    ap = _market_candidate(url="https://apnews.com/article/stocks-abc123")
    cnbc = _market_candidate(url="https://www.cnbc.com/2026/11/03/stocks.html")

    assert _quality_rank(ap) == _quality_rank(cnbc) - 3


def test_rank_puts_off_topic_stories_behind_market_coverage():
    newer_sports = _market_candidate(
        title="Cavaliers beat 76ers 119-95",
        url="https://apnews.com/article/cavaliers-76ers-abc123",
        published_date=datetime(2026, 11, 6, 12, 0, tzinfo=UTC),
        raw_content="LeBron James scored 30 points. " * 30,
    )
    older_market = _market_candidate(
        title="Treasury yields slide as traders weigh the election",
        url="https://www.cnbc.com/2026/11/03/yields.html",
        published_date=datetime(2026, 11, 3, 12, 0, tzinfo=UTC),
    )

    assert rank([newer_sports, older_market])[0] is older_market


def test_select_caps_each_publisher_counting_subdomains_as_one():
    title = "Helsinki stocks close higher"
    candidates = [
        _market_candidate(title=title, url="https://de.marketscreener.com/news/1"),
        _market_candidate(title=title, url="https://es.marketscreener.com/news/2"),
        _market_candidate(title=title, url="https://nl.marketscreener.com/news/3"),
        _market_candidate(title=title, url="https://www.reuters.com/markets/a-2026-11-03/"),
        _market_candidate(title=title, url="https://www.inderes.fi/news/4"),
    ]

    selected = _select(candidates, market="FI", top_k=4)

    assert [c.url for c in selected] == [
        "https://de.marketscreener.com/news/1",
        "https://es.marketscreener.com/news/2",
        "https://www.reuters.com/markets/a-2026-11-03/",
        "https://www.inderes.fi/news/4",
    ]


def test_select_backfills_past_the_cap_when_the_pool_is_small():
    candidates = [_market_candidate(url=f"https://apnews.com/article/story-{i}") for i in range(4)]

    assert len(_select(candidates, top_k=4)) == 4


def test_select_prefers_allowlisted_over_uncapped_backfill():
    allowlisted = [_market_candidate(url=f"https://apnews.com/article/story-{i}") for i in range(3)]
    other = _market_candidate(url="https://example.com/2026/11/03/story")

    selected = _select([*allowlisted, other], top_k=2)

    assert {c.url for c in selected} == {a.url for a in allowlisted[:2]}


def test_select_covers_each_trading_day_with_a_session_wrap_in_order():
    newest_news = [
        _market_candidate(
            title=f"Treasury yields story {i}",
            url=f"https://apnews.com/article/yields-{i}",
            published_date=datetime(2026, 11, 6, 12, 0, tzinfo=UTC),
        )
        for i in range(4)
    ]
    wraps = [
        _market_candidate(
            title=f"Wrap {day}",
            url=f"https://www.{publisher}/2026/11/0{day}/wrap.html",
            published_date=datetime(2026, 11, day, 12, 0, tzinfo=UTC),
            raw_content=WRAP_TEXT,
        )
        for day, publisher in ((2, "cnbc.com"), (3, "nytimes.com"), (4, "reuters.com"))
    ]

    selected = _select([*newest_news, *wraps], top_k=5)

    assert [c.title for c in selected[:3]] == ["Wrap 2", "Wrap 3", "Wrap 4"]


def test_select_dates_wraps_by_title_then_url_before_brave_date():
    # Brave dates all of these Nov 6; the title and URL say which session each one covers.
    brave_date = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    title_dated = _market_candidate(
        title="Stock market news for Nov. 4, 2026",
        url="https://www.cnbc.com/2026/11/03/stock-market-today-live-updates.html",
        published_date=brave_date,
        raw_content=WRAP_TEXT,
    )
    url_dated = _market_candidate(
        title="Stocks erase rally",
        url="https://www.nytimes.com/2026/11/05/business/stocks.html",
        published_date=brave_date,
        raw_content=WRAP_TEXT,
    )

    selected = _select([url_dated, title_dated], top_k=2)

    assert [c.title for c in selected] == ["Stock market news for Nov. 4, 2026", "Stocks erase rally"]


def test_select_prefers_week_totals_for_the_last_session():
    longer = _market_candidate(
        title="Stocks mixed Friday",
        url="https://www.cnbc.com/2026/11/06/stocks.html",
        published_date=datetime(2026, 11, 6, 12, 0, tzinfo=UTC),
        raw_content=WRAP_TEXT * 2,
    )
    week_totals = _market_candidate(
        title="How major US stock indexes fared Friday 11/6/2026",
        url="https://apnews.com/article/wall-street-stocks-dow-nasdaq-abc123",
        published_date=datetime(2026, 11, 6, 12, 0, tzinfo=UTC),
        raw_content=f"{WRAP_TEXT} For the week, the S&P 500 fell 2%.",
    )

    assert _select([longer, week_totals], top_k=1)[0] is week_totals


def test_select_leaves_stories_dated_outside_the_period_for_last():
    stale_minutes = _market_candidate(
        title="Minutes of the Federal Open Market Committee",
        url="https://www.federalreserve.gov/monetarypolicy/fomcminutes20260916.htm",
    )
    next_week = _market_candidate(
        title="Stock market next week: Outlook for Nov. 9-13, 2026",
        url="https://www.cnbc.com/2026/11/06/stock-market-next-week.html",
    )
    current = _market_candidate(url="https://www.reuters.com/markets/us/stocks-2026-11-03/")

    assert [c.url for c in _select([stale_minutes, next_week, current], top_k=1)] == [current.url]
    assert len(_select([stale_minutes, next_week, current], top_k=3)) == 3


def test_select_ignores_undated_hub_pages_as_session_wraps():
    hub = _market_candidate(
        title="U.S. Stock Market Headlines",
        url="https://www.reuters.com/markets/us/",
        published_date=datetime(2026, 11, 3, 12, 0, tzinfo=UTC),
        raw_content=WRAP_TEXT * 2,
    )
    wrap = _market_candidate(
        url="https://www.cnbc.com/2026/11/03/stocks.html",
        raw_content=WRAP_TEXT,
    )

    assert _select([hub, wrap], top_k=1)[0] is wrap


def test_select_treats_top_k_as_a_ceiling_and_skips_weak_pages_above_the_minimum():
    good = [
        _market_candidate(url=f"https://www.{publisher}/2026/11/03/stocks.html")
        for publisher in ("cnbc.com", "nytimes.com", "reuters.com", "bloomberg.com", "ft.com", "apnews.com")
    ]
    hub = _market_candidate(title="Stock Market Headlines", url="https://www.wsj.com/markets/stocks/")
    data_stub = _market_candidate(
        title="Stocks data release",
        url="https://www.nasdaq.com/2026/11/03/data",
        raw_content="x" * 100,
    )
    off_topic = _market_candidate(
        title="Trump replaces chief of staff",
        url="https://www.barrons.com/2026/11/03/politics",
    )

    selected = _select([hub, data_stub, off_topic, *good], top_k=10)

    assert [c.url for c in selected] == [c.url for c in good]


def test_select_uses_weak_pages_only_to_reach_the_minimum():
    good = [_market_candidate(url=f"https://www.{p}/2026/11/03/stocks.html") for p in ("cnbc.com", "nytimes.com")]
    weak = [_market_candidate(title="Headlines", url=f"https://www.{p}/section/") for p in ("wsj.com", "ft.com")]
    repeats = [_market_candidate(url=f"https://www.cnbc.com/2026/11/03/stocks-{i}.html") for i in range(3)]

    selected = _select([*good, *repeats, *weak], top_k=10)

    assert len(selected) == 5
    assert [c.url for c in selected[:2]] == [c.url for c in good]


def test_select_vn_keeps_its_plain_top_five():
    ranked = [
        _market_candidate(
            title="VN-Index giảm",
            url=f"https://cafef.vn/khoi-ngoai-18825110{i}.chn",
            published_date=datetime(2026, 11, 2 + i, 12, 0, tzinfo=UTC),
            raw_content="VN-Index giảm hơn 15 điểm. " * 40,
        )
        for i in range(5)
    ] + [_market_candidate(title="Tin khác", url="https://vietstock.vn/tin-khac-123456.htm")]

    selected = _select(ranked, market="VN", top_k=10)

    assert [c.url for c in selected] == [c.url for c in ranked[:5]]


def test_rank_for_vn_keeps_its_article_path_reward_and_no_topic_penalty():
    article = _market_candidate(title="Tin tức", url="https://vir.com.vn/article/tin-tuc")
    other = _market_candidate(title="VN-Index giảm", url="https://cafef.vn/tin-tuc")

    assert rank([other, article], market="VN")[0] is article
