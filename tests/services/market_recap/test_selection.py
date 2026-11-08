"""Characterization tests for market recap source selection (behaviour of PR #76, judged 11/11 better).

Inputs to `select_candidates` are already ranked, so each test passes candidates in the rank order it wants.
"""

from datetime import UTC, date, datetime

import pytest

from services.market_recap.ranking import rank
from services.market_recap.schemas import Candidate
from services.market_recap.selection import select_candidates

WRAP = "The S&P 500 rose 1% and the Dow gained 200 points while the Nasdaq climbed. " * 12
WEEK_TOTAL = " For the week, the S&P 500 fell 2%."
STORY = "Treasury yields rose as investors weighed the jobs report. " * 10
STORY_PUBLISHERS = ("wsj.com", "barrons.com", "marketwatch.com", "bloomberg.com", "nasdaq.com")
FI_STORY_PUBLISHERS = (
    "inderes.fi",
    "marketscreener.com",
    "tradingeconomics.com",
    "globenewswire.com",
    "morningstar.com",
)


def _on(day: int, month: int = 11, year: int = 2026) -> datetime:
    return datetime(year, month, day, 12, 0, tzinfo=UTC)


def _page(
    url: str,
    *,
    title: str = "Stocks close higher",
    body: str = STORY,
    published: datetime | None = _on(3),
    score: float = 0.0,
) -> Candidate:
    return Candidate(title=title, url=url, published_date=published, raw_content=body, score=score, provider="brave")


def _stories(count: int = 5, *, published: datetime = _on(3)) -> list[Candidate]:
    """On-topic US stories from distinct publishers: enough of them means no backfill."""
    return [
        _page(f"https://www.{p}/articles/story-{i}", published=published)
        for i, p in enumerate(STORY_PUBLISHERS[:count])
    ]


def _fi_stories() -> list[Candidate]:
    return [
        _page(f"https://www.{p}/news/story-{i}", title="Helsinki stocks rise", body="Shares rose. " * 30)
        for i, p in enumerate(FI_STORY_PUBLISHERS)
    ]


def _select(
    candidates: list[Candidate],
    *,
    market: str = "US",
    top_k: int = 10,
    period: tuple[date, date] = (date(2026, 11, 2), date(2026, 11, 6)),  # Mon..Fri
) -> list[Candidate]:
    return select_candidates(candidates, market=market, period_start=period[0], period_end=period[1], top_k=top_k)


def _urls(candidates: list[Candidate]) -> list[str]:
    return [c.url for c in candidates]


# --- What counts as a session wrap -------------------------------------------------------------------------


def test_us_wrap_needs_two_distinct_index_names():
    dow_only = _page("https://www.cnbc.com/2026/11/03/dow.html", body="The Dow rose 200 points. " * 40)
    wrap = _page("https://www.nytimes.com/2026/11/03/stocks.html", body=WRAP)

    assert _select([dow_only, wrap], top_k=1) == [wrap]


def test_fi_wrap_needs_one_index_name():
    finnish_story = _page(
        "https://www.inderes.fi/news/finnish-stocks-1",
        title="Finnish stocks rise",
        body="Finland's market gained as investors bought industrials. " * 10,
    )
    wrap = _page(
        "https://www.reuters.com/markets/helsinki-close-3",
        title="Helsinki close",
        body="The OMX Helsinki index rose 1%. " * 20,
    )

    assert _select([finnish_story, wrap], market="FI", top_k=1) == [wrap]


def test_unknown_market_has_no_wraps_and_no_topic_check():
    sports = _page(
        "https://www.cnbc.com/2026/11/03/cavs.html", title="Cavaliers beat 76ers", body="LeBron scored. " * 50
    )
    wrap = _page("https://www.nytimes.com/2026/11/03/stocks.html", body=WRAP)

    assert _select([sports, wrap], market="XX", top_k=1) == [sports]
    assert _select([sports, wrap], market="US", top_k=1) == [wrap]


def test_market_code_is_case_insensitive():
    sports = _page(
        "https://www.cnbc.com/2026/11/03/cavs.html", title="Cavaliers beat 76ers", body="LeBron scored. " * 50
    )
    wrap = _page("https://www.nytimes.com/2026/11/03/stocks.html", body=WRAP)

    assert _select([sports, wrap], market="us", top_k=1) == [wrap]


def test_video_pages_are_neither_wraps_nor_stories():
    video = _page("https://www.reuters.com/video/2026/11/03/stocks", body=WRAP)
    stories = _stories()

    assert _select([video, *stories]) == stories


# --- What counts as an on-topic story ----------------------------------------------------------------------


@pytest.mark.parametrize(("chars", "included"), [(299, False), (300, True)])
def test_story_needs_300_chars(chars: int, included: bool):
    candidate = _page("https://www.ft.com/content/story-9", body="x" * chars)
    stories = _stories()

    assert _select([candidate, *stories]) == ([candidate, *stories] if included else stories)


def test_us_story_needs_a_market_word_in_its_title():
    body_only = _page(
        "https://www.ft.com/content/story-9", title="Apple unveils new iPhone", body=f"Apple stocks moved. {STORY}"
    )
    stories = _stories()

    assert _select([body_only, *stories]) == stories


@pytest.mark.parametrize(
    ("url", "included"),
    [
        ("https://www.bloomberg.com/news/articles/2026-11-03/europe-stocks", False),
        ("https://www.bloomberg.com/news/articles/2026-11-03/helsinki-stocks", True),
    ],
)
def test_fi_story_needs_a_finnish_hint_anywhere_including_the_url(url: str, included: bool):
    candidate = _page(url, title="European stocks rise", body="Shares in Europe rose. " * 20)
    stories = _fi_stories()

    assert _select([candidate, *stories], market="FI") == ([candidate, *stories] if included else stories)


# --- Eligibility: allowlist and session date ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("published", "included"),
    [(_on(31, month=10), False), (_on(1), True), (_on(7), True), (_on(8), False)],
)
def test_pages_dated_one_day_outside_the_period_are_still_eligible(published: datetime, included: bool):
    candidate = _page("https://apnews.com/article/story-9", published=published)
    stories = _stories()

    assert _select([candidate, *stories]) == ([candidate, *stories] if included else stories)


def test_non_allowlisted_page_is_used_only_as_backfill_after_selected_pages():
    other = _page("https://example.com/2026/11/03/stocks")
    stories = _stories(4)

    assert _select([other, *stories]) == [*stories, other]


def test_undated_page_is_used_only_as_backfill():
    undated = _page("https://apnews.com/article/story-7", published=None)
    stories = _stories(4)

    assert _select([undated, *stories]) == [*stories, undated]


# --- Session date inference --------------------------------------------------------------------------------


def test_title_date_without_year_takes_the_previous_year_across_new_year():
    wrap = _page(
        "https://www.cnbc.com/markets/wrap-1",
        title="Wall Street closes out the year, Dec. 31",
        body=WRAP,
        published=_on(1, 1, 2027),
    )
    stories = _stories(published=_on(29, 12))

    selected = _select([*stories, wrap], period=(date(2026, 12, 28), date(2027, 1, 1)))

    assert selected == [wrap, *stories]


def test_url_dates_parse_with_dashes_or_no_separators():
    dashed = _page("https://www.cnbc.com/markets/wrap-2026-11-05", body=WRAP, published=_on(6))
    compact = _page("https://www.federalreserve.gov/newsevents/wrap20261104.htm", body=WRAP, published=_on(6))

    assert _select([dashed, compact]) == [compact, dashed]


def test_numeric_title_date_dates_us_pages():
    titled = _page(
        "https://apnews.com/article/indexes-1",
        title="How major US stock indexes fared Wednesday 11/4/2026",
        body=WRAP,
        published=_on(6),
    )
    url_dated = _page("https://www.cnbc.com/2026/11/05/stocks.html", body=WRAP, published=_on(6))

    assert _select([url_dated, titled]) == [titled, url_dated]


def test_invalid_title_date_falls_back_to_the_url_date():
    bad_title = _page(
        "https://www.cnbc.com/2026/11/04/stocks.html", title="Stocks on Feb. 30", body=WRAP, published=_on(6)
    )
    later = _page("https://www.nytimes.com/2026/11/05/stocks.html", body=WRAP)

    assert _select([later, bad_title]) == [bad_title, later]


def test_invalid_url_date_falls_back_to_the_brave_date():
    bad_url = _page("https://www.nytimes.com/2026/02/30/stocks.html", body=WRAP, published=_on(3))
    later = _page("https://www.cnbc.com/2026/11/05/stocks.html", body=WRAP)

    assert _select([later, bad_url]) == [bad_url, later]


# --- Trading days ------------------------------------------------------------------------------------------


def test_weekend_only_period_takes_wraps_from_weekend_days():
    story = _page("https://www.cnbc.com/markets/story-1", published=_on(7))
    sunday = _page("https://www.reuters.com/markets/2026/11/08/wrap", body=WRAP)
    saturday = _page("https://www.nytimes.com/2026/11/07/stocks.html", body=WRAP)

    assert _select([story, sunday, saturday], period=(date(2026, 11, 7), date(2026, 11, 8))) == [
        saturday,
        sunday,
        story,
    ]


def test_weekend_page_inside_a_week_is_a_story_not_a_wrap():
    story = _page("https://www.cnbc.com/markets/story-1", published=_on(5))
    saturday = _page("https://www.nytimes.com/2026/11/07/stocks.html", body=WRAP)
    thursday = _page("https://www.reuters.com/markets/2026/11/05/wrap", body=WRAP)

    selected = _select([story, saturday, thursday], period=(date(2026, 11, 2), date(2026, 11, 8)))  # Mon..Sun

    assert selected == [thursday, story, saturday]


# --- Picking wraps -----------------------------------------------------------------------------------------


def test_one_wrap_per_day_picks_the_longest_and_the_rest_join_as_stories():
    longer = _page("https://www.cnbc.com/2026/11/04/stocks.html", body=WRAP * 2)
    shorter = _page("https://www.nytimes.com/2026/11/04/stocks.html", body=WRAP)
    story = _page("https://www.reuters.com/markets/story-1")

    assert _select([story, shorter, longer]) == [longer, story, shorter]


def test_wraps_of_equal_length_keep_rank_order():
    first = _page("https://www.cnbc.com/2026/11/04/stocks.html", body=WRAP)
    second = _page("https://www.nytimes.com/2026/11/04/stocks.html", body=WRAP)

    assert _select([first, second], top_k=1) == [first]
    assert _select([second, first], top_k=1) == [second]


def test_latest_day_gets_first_pick_of_publisher_slots():
    wraps = {day: _page(f"https://www.cnbc.com/2026/11/0{day}/wrap.html", body=WRAP) for day in (2, 3, 6)}
    stories = _stories(3)

    assert _select([wraps[2], wraps[3], wraps[6], *stories]) == [wraps[3], wraps[6], *stories]


def test_wrap_prefers_the_publisher_used_least_so_far():
    cnbc_longer = _page("https://www.cnbc.com/2026/11/05/stocks.html", body=WRAP * 2)
    nyt = _page("https://www.nytimes.com/2026/11/05/stocks.html", body=WRAP)
    cnbc_friday = _page("https://www.cnbc.com/2026/11/06/stocks.html", body=WRAP)

    assert _select([cnbc_longer, nyt, cnbc_friday]) == [nyt, cnbc_friday, cnbc_longer]


def test_week_total_preference_applies_only_to_the_first_wrap_picked():
    friday = _page("https://www.reuters.com/markets/2026/11/06/wrap", body=WRAP)
    thursday_longer = _page("https://www.cnbc.com/2026/11/05/stocks.html", body=WRAP * 2)
    thursday_totals = _page("https://www.nytimes.com/2026/11/05/stocks.html", body=WRAP + WEEK_TOTAL)

    assert _select([thursday_longer, thursday_totals, friday]) == [thursday_longer, friday, thursday_totals]


def test_single_day_period_ignores_week_totals():
    longer = _page("https://www.cnbc.com/2026/11/06/stocks.html", body=WRAP * 2)
    totals = _page("https://apnews.com/article/indexes-abc123", body=WRAP + WEEK_TOTAL, published=_on(6))

    assert _select([totals, longer], top_k=1, period=(date(2026, 11, 6), date(2026, 11, 6))) == [longer]


def test_top_k_caps_wraps_keeping_the_latest_days():
    publishers = ("cnbc.com", "nytimes.com", "reuters.com", "bloomberg.com", "wsj.com")
    wraps = [_page(f"https://www.{p}/2026/11/0{day}/wrap.html", body=WRAP) for day, p in zip(range(2, 7), publishers)]

    assert _select(wraps, top_k=2) == wraps[3:]


# --- Backfill ----------------------------------------------------------------------------------------------


def _backfill_pool() -> list[Candidate]:
    other = _page("https://example.com/2026/11/03/stocks")
    old = _page("https://www.nytimes.com/markets/story-9", published=_on(20, month=10))
    cnbc = [_page(f"https://www.cnbc.com/markets/story-{i}") for i in range(3)]
    return [other, cnbc[0], old, cnbc[1], cnbc[2]]


def test_backfill_takes_the_full_ranked_list_in_order_ignoring_the_publisher_cap():
    other, cnbc0, old, cnbc1, cnbc2 = _backfill_pool()

    assert _select([other, cnbc0, old, cnbc1, cnbc2]) == [cnbc0, cnbc1, other, old, cnbc2]


def test_backfill_stops_at_top_k_when_it_is_below_the_minimum():
    other, cnbc0, old, cnbc1, cnbc2 = _backfill_pool()

    assert _select([other, cnbc0, old, cnbc1, cnbc2], top_k=3) == [cnbc0, cnbc1, other]


@pytest.mark.parametrize("market", ["US", "FI", "VN", "XX"])
def test_empty_pool_selects_nothing(market: str):
    assert _select([], market=market) == []


def test_vn_takes_its_ranked_top_k_when_below_the_minimum_without_filtering():
    ranked = [_page("https://example.com/tin-1", title="Tin khác")] + [
        _page(f"https://cafef.vn/tin-{i}.chn", title="VN-Index giảm") for i in range(5)
    ]

    assert _select(ranked, market="VN", top_k=3) == ranked[:3]


# --- Ranking signals that move into criteria ---------------------------------------------------------------


def test_rank_puts_body_only_topic_between_on_topic_and_off_topic():
    off_topic = _page("https://apnews.com/article/a-1", title="Apple event", body="y" * 900)
    body_only = _page("https://apnews.com/article/b-1", title="Apple event", body="stocks " + "x" * 893)
    on_topic = _page("https://apnews.com/article/c-1", title="Stocks rally", body="x" * 900)

    assert rank([off_topic, body_only, on_topic]) == [on_topic, body_only, off_topic]


def test_rank_fi_counts_a_finnish_hint_in_the_url():
    no_hint = _page("https://www.inderes.fi/news/a-1", title="Market update", body="z" * 900)
    url_hint = _page("https://www.inderes.fi/news/helsinki-1", title="Market update", body="z" * 900)

    assert rank([no_hint, url_hint], market="FI")[0] is url_hint


# --- Why each source was picked ----------------------------------------------------------------------------


def _selection_log(caplog: pytest.LogCaptureFixture) -> dict:
    [record] = [r for r in caplog.records if getattr(r, "event", None) == "recap.sources.selected"]
    return record.fields


def test_logs_why_each_source_was_picked(caplog: pytest.LogCaptureFixture):
    wrap = _page("https://www.cnbc.com/2026/11/04/stocks.html", body=WRAP)
    story = _page("https://www.reuters.com/markets/story-1")
    other = _page("https://example.com/2026/11/03/stocks")

    with caplog.at_level("INFO"):
        _select([other, story, wrap])

    assert _selection_log(caplog) == {
        "market": "US",
        "count": 3,
        "sources": ["wrap:2026-11-04 cnbc.com", "story reuters.com", "backfill example.com"],
    }


def test_logs_plain_top_sources_for_vn(caplog: pytest.LogCaptureFixture):
    ranked = [_page(f"https://cafef.vn/tin-{i}.chn", title="VN-Index giảm") for i in range(2)]

    with caplog.at_level("INFO"):
        _select(ranked, market="vn")

    assert _selection_log(caplog) == {"market": "VN", "count": 2, "sources": ["top cafef.vn", "top cafef.vn"]}
