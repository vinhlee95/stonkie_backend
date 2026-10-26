from datetime import UTC, datetime

import pytest

from services.analyze_retrieval.relevance import (
    company_aliases,
    dedupe_by_title,
    filter_off_topic,
    merge_sources,
    normalize_title,
)
from services.analyze_retrieval.schemas import AnalyzePassage, AnalyzeSource
from services.market_recap.schemas import Candidate


def _candidate(title: str, raw_content: str = "", url: str | None = None) -> Candidate:
    return Candidate(
        title=title,
        url=url or f"https://example.com/{abs(hash(title))}",
        raw_content=raw_content,
        provider="brave",
    )


def _source(source_id: str, title: str) -> AnalyzeSource:
    return AnalyzeSource(
        id=source_id,
        url=f"https://example.com/{source_id}",
        title=title,
        publisher="Example",
        published_at=datetime(2026, 10, 1, tzinfo=UTC),
        is_trusted=True,
    )


def _passage(source_id: str, index: int = 0) -> AnalyzePassage:
    return AnalyzePassage(
        source_id=source_id,
        url=f"https://example.com/{source_id}",
        title=source_id,
        publisher="Example",
        is_trusted=True,
        passage_index=index,
        content=f"passage {source_id} {index}",
    )


@pytest.mark.parametrize(
    ("company_name", "ticker", "expected"),
    [
        ("Amazon.com, Inc.", "AMZN", {"amazon"}),
        ("The Coca-Cola Company", "KO", {"coca cola", "coca", "cola"}),
        ("Ford Motor Company", "F", {"ford motor", "ford"}),
        ("JPMorgan Chase & Co.", "JPM", {"jpmorgan chase", "jpmorgan"}),
        ("Bank of America Corporation", "BAC", {"bank of america"}),
        ("AAPL", "AAPL", set()),
        (None, "AAPL", set()),
    ],
)
def test_company_aliases(company_name, ticker, expected):
    assert company_aliases(company_name, ticker) == expected


def test_filter_off_topic_drops_articles_about_other_companies():
    meta = _candidate("Earnings call transcript: Meta beats Q3 2026 estimates", "Meta said ad revenue grew.")
    kept = [
        _candidate("Should You Buy Apple Stock Now?", "Analysts like the iPhone cycle."),
        _candidate("Why is the stock climbing today?", "Apple rose 3%. Apple's services hit a record."),
        _candidate("AAPL quote", "Shares traded higher."),
    ]

    result, dropped = filter_off_topic([meta, *kept], company_name="Apple Inc.", ticker="AAPL")

    assert result == kept
    assert dropped == 1


def test_filter_off_topic_needs_two_body_mentions_without_a_title_mention():
    passing_mention = _candidate("Turkey cuts interest rates", "Economists at JPMorgan expected the cut.")
    on_topic = [
        _candidate("JPMorgan's Dimon says US economy booming", "Jamie Dimon spoke on Tuesday."),
        _candidate("Bank chief warns on inflation", "JPMorgan Chase CEO said JPMorgan sees risks."),
    ]

    result, dropped = filter_off_topic([passing_mention, *on_topic], company_name="JPMorgan Chase & Co.", ticker="JPM")

    assert result == on_topic
    assert dropped == 1


def test_filter_off_topic_keeps_filings_named_by_ticker_in_the_url():
    filing = _candidate(
        "cost-20260830", "Membership fee income was $5.3 billion.", url="https://www.sec.gov/x/cost-20260830.htm"
    )
    others = [_candidate("Costco comps beat", "Costco grew."), _candidate("Costco fee hike", "Costco raised fees.")]

    result, dropped = filter_off_topic([filing, *others], company_name="Costco Wholesale Corporation", ticker="COST")

    assert result == [filing, *others]
    assert dropped == 0


def test_filter_off_topic_drops_a_sibling_company_sharing_the_name():
    sibling = _candidate(
        "Coca-Cola Consolidated, Inc. Announces Fourth Quarter Dividend",
        "Coca-Cola Consolidated, Inc. (NASDAQ: COKE) declared a $0.25 dividend. Coca-Cola Consolidated said.",
    )
    kept = [
        _candidate("Coca-Cola declares quarterly dividend", "The Coca-Cola Company (NYSE:KO) declared 53 cents."),
        _candidate("Coca-Cola dividend history", "Coca-Cola pays $2.12 a year."),
    ]

    result, dropped = filter_off_topic([sibling, *kept], company_name="The Coca-Cola Company", ticker="KO")

    assert result == kept
    assert dropped == 1


def test_filter_off_topic_treats_another_share_class_as_the_same_company():
    share_class = _candidate("Alphabet Inc. (NASDAQ: GOOG) wins ruling", "Alphabet Inc. (NASDAQ: GOOG) won.")
    other = _candidate("Alphabet antitrust update", "Alphabet faces damages.")

    result, dropped = filter_off_topic([share_class, other], company_name="Alphabet Inc.", ticker="GOOGL")

    assert result == [share_class, other]
    assert dropped == 0


def test_filter_off_topic_keeps_everything_when_fewer_than_two_would_remain():
    candidates = [_candidate("Apple stock", "Apple rose."), _candidate("Market wrap", "Stocks fell.")]

    result, dropped = filter_off_topic(candidates, company_name="Apple Inc.", ticker="AAPL")

    assert result == candidates
    assert dropped == 0


def test_filter_off_topic_is_a_no_op_without_a_real_company_name():
    candidates = [_candidate("Market wrap", "Stocks fell."), _candidate("Rates", "Yields rose.")]

    assert filter_off_topic(candidates, company_name="AAPL", ticker="AAPL") == (candidates, 0)
    assert filter_off_topic(candidates, company_name=None, ticker=None) == (candidates, 0)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Meta Falls After Boosting Spending Plans - Bloomberg", "meta falls after boosting spending plans"),
        ("Why is Netflix stock sliding today? By Investing.com", "why is netflix stock sliding today"),
        ("JPMorgan announces $5 billion in investments By Reuters", "jpmorgan announces 5 billion in investments"),
        ("Walt Disney (NYSE:DIS) Stock Forecast | Simply Wall St", "walt disney nyse dis stock forecast"),
    ],
)
def test_normalize_title_strips_publisher_tags_and_punctuation(title, expected):
    assert normalize_title(title) == expected


def test_dedupe_by_title_keeps_one_copy_in_place_and_swaps_in_the_better_one():
    items = [
        ("Coca-Cola declares quarterly dividend of 53 cents By Investing.com", 1),
        ("Something else", 5),
        ("Coca-Cola declares quarterly dividend of 53 cents - Yahoo Finance", 3),
        ("", 0),
        ("", 0),
    ]

    result = dedupe_by_title(items, title=lambda item: item[0], better=lambda new, kept: new[1] > kept[1])

    assert result == [items[2], items[1], items[3], items[4]]


def test_merge_sources_lists_an_article_found_for_several_tickers_once():
    tesla = [_source("t1", "Tesla vs Ford: which is the better buy?"), _source("t2", "Tesla deliveries")]
    ford = [
        _source("t1", "Tesla vs Ford: which is the better buy?"),
        _source("f2", "Tesla vs Ford: Which Is The Better Buy? - Yahoo Finance"),
        _source("f3", "Ford earnings"),
    ]

    sources, passages = merge_sources(
        [
            (tesla, [_passage("t1"), _passage("t2")]),
            (ford, [_passage("t1"), _passage("t1", 1), _passage("f2"), _passage("f3")]),
        ]
    )

    assert [s.id for s in sources] == ["t1", "t2", "f3"]
    assert [(p.source_id, p.passage_index) for p in passages] == [("t1", 0), ("t2", 0), ("t1", 1), ("f3", 0)]
