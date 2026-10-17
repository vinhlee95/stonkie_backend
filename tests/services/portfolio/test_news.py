from services.portfolio import news


def story(title="Apple jumps", link="https://example.com/a", published=1_792_261_800, related=("AAPL",), **extra):
    return {
        "type": "STORY",
        "title": title,
        "publisher": "CNBC",
        "link": link,
        "providerPublishTime": published,
        "relatedTickers": list(related),
        **extra,
    }


def item(url, published_at, related, headline="h"):
    return {"headline": headline, "source": "s", "url": url, "published_at": published_at, "related": related}


def test_queries_fall_back_to_name_for_suffixed_tickers():
    assert news.queries("AAPL", "Apple Inc.") == ["AAPL"]
    assert news.queries("RR.L", "Rolls-Royce Holdings") == ["RR.L", "Rolls-Royce Holdings"]
    assert news.queries("RR.L", None) == ["RR.L"]


def test_parse_maps_story_fields():
    assert news.parse([story(title="  Apple jumps  ")]) == [
        {
            "headline": "Apple jumps",
            "source": "CNBC",
            "url": "https://example.com/a",
            "published_at": "2026-10-17T18:30:00Z",
            "related": ["AAPL"],
        }
    ]


def test_parse_drops_videos_and_incomplete_stories():
    raw = [
        story(type="VIDEO"),
        story(title=""),
        story(link="javascript:alert(1)"),
        story(published=None),
        story(published=True),
        "not a dict",
        {**story(), "relatedTickers": None, "publisher": None},
    ]

    parsed = news.parse(raw)

    assert len(parsed) == 1
    assert parsed[0]["related"] == [] and parsed[0]["source"] == ""


def test_is_about_needs_yahoo_tag_and_headline_mention():
    assert news.is_about(item("u", "t", ["AAPL", "MSFT"], headline="Apple jumps"), "AAPL", "Apple Inc.")
    assert news.is_about(item("u", "t", ["NVDA"], headline="Nvidia (NVDA) slides"), "NVDA", None)
    assert news.is_about(item("u", "t", ["SAP"], headline="SAP lifts outlook"), "SAP.DE", "SAP SE")
    assert news.is_about(
        item("u", "t", ["RR.L"], headline="Rolls Royce wins order"), "RR.L", "Rolls-Royce Holdings plc"
    )
    # Tagged but not named: Yahoo tags NVDA on stories that only mention it in passing.
    assert not news.is_about(item("u", "t", ["AMZN", "NVDA"], headline="Buy Amazon now?"), "NVDA", "NVIDIA Corporation")
    # Named but not tagged.
    assert not news.is_about(item("u", "t", ["MSFT"], headline="Apple jumps"), "AAPL", "Apple Inc.")


def test_from_cache_rejects_invalid_entries():
    good = [item("u", "2026-10-16T18:30:00Z", ["AAPL"])]
    assert news.from_cache("AAPL", news.to_json(good)) == good
    assert news.from_cache("AAPL", {"items": []}) == []
    assert news.from_cache("AAPL", None) is None
    assert news.from_cache("AAPL", {"items": [{"headline": "x"}]}) is None
    assert news.from_cache("AAPL", {"nope": 1}) is None


def test_merge_dedupes_tags_and_sorts_newest_first():
    shared = item("https://x/shared", "2026-10-16T10:00:00Z", ["NVDA", "AAPL"], headline="Apple and Nvidia rally")
    per_ticker = {
        "AAPL": [item("https://x/old", "2026-10-15T10:00:00Z", ["AAPL"], headline="Apple slips"), shared],
        "NVDA": [shared, item("https://x/new", "2026-10-16T12:00:00Z", ["NVDA"], headline="NVDA up")],
    }

    merged = news.merge(per_ticker, {"AAPL": "Apple Inc.", "NVDA": "NVIDIA Corporation"}, limit=10)

    assert [m["url"] for m in merged] == ["https://x/new", "https://x/shared", "https://x/old"]
    assert merged[1]["tickers"] == ["AAPL", "NVDA"]  # holding order, not Yahoo's
    assert set(merged[0]) == {"headline", "source", "url", "published_at", "tickers"}


def test_merge_caps_at_limit():
    per_ticker = {"AAPL": [item(f"https://x/{i}", f"2026-10-1{i}T00:00:00Z", ["AAPL"], "AAPL") for i in range(5)]}

    assert [m["url"] for m in news.merge(per_ticker, {"AAPL": None}, limit=2)] == ["https://x/4", "https://x/3"]
