"""The LLM curator picks recap sources from the filtered pool; code keeps a few guarantees. A fake agent is the LLM."""

import json
from datetime import UTC, date, datetime

import pytest

from services.market_recap.curation import (
    CuratorError,
    build_catalog,
    build_curator_prompt,
    curate_sources,
    parse_curator_output,
)
from services.market_recap.schemas import Candidate

WEEK = (date(2026, 11, 2), date(2026, 11, 6))  # Mon..Fri


def _on(day: int) -> datetime:
    return datetime(2026, 11, day, 12, 0, tzinfo=UTC)


def _page(url: str, *, title: str = "Stocks close higher", body: str = "Stocks rose. " * 30, day: int = 3) -> Candidate:
    return Candidate(title=title, url=url, published_date=_on(day), raw_content=body, provider="brave")


def _answer(*picks: tuple[str, str], would_search: list[dict] | None = None) -> str:
    items = ",".join(f'{{"id":"{pid}","role":"{role}","why":"reason"}}' for pid, role in picks)
    return f'Thinking done. [CURATOR_JSON]{{"picks":[{items}],"would_search":{json.dumps(would_search or [])}}}[/CURATOR_JSON]'


class FakeAgent:
    """Returns one queued answer per call; a queued exception is raised instead."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.options: list[dict] = []
        self.model_name = "fake-curator"

    def generate_content(self, prompt: str, use_google_search: bool = False, **options):
        self.prompts.append(prompt)
        self.options.append(options)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return [answer[: len(answer) // 2], {"type": "url_citation", "url": "x"}, answer[len(answer) // 2 :]]


def _pool() -> list[Candidate]:
    return [
        _page("https://www.reuters.com/markets/us/stocks-2026-11-04/", title="Wall St ends higher", day=5),
        _page("https://apnews.com/article/stocks-nov-3", title="How major US stock indexes fared Nov. 3", day=4),
        _page("https://www.cnbc.com/2026/11/02/stock-market-today.html", title="Dow rises", day=2),
        _page("https://www.cnbc.com/2026/11/02/oil.html", title="Oil jumps", day=2),
        _page("https://www.wsj.com/markets/fed-holds", title="Fed holds rates", day=6),
    ]


def _curate(agent: FakeAgent, pool: list[Candidate] | None = None, **overrides):
    kwargs = {"market": "US", "cadence": "weekly", "period_start": WEEK[0], "period_end": WEEK[1], "agent": agent}
    return curate_sources(pool if pool is not None else _pool(), **{**kwargs, **overrides})


def _ids(catalog) -> dict[str, str]:
    return {entry.id: entry.candidate.url for entry in catalog}


# --- Catalog ---------------------------------------------------------------------------------------------------


def test_catalog_orders_by_session_date_then_publisher_with_undated_last():
    undated = Candidate(title="Markets", url="https://www.ft.com/markets", raw_content="x", provider="brave")
    catalog = build_catalog([*_pool(), undated], market="US", period_end=WEEK[1])

    assert [(e.id, e.session_date, e.publisher) for e in catalog] == [
        ("S1", date(2026, 11, 2), "cnbc.com"),
        ("S2", date(2026, 11, 2), "cnbc.com"),
        ("S3", date(2026, 11, 3), "apnews.com"),  # title date beats Brave's date (Nov 4)
        ("S4", date(2026, 11, 4), "reuters.com"),  # URL date beats Brave's date (Nov 5)
        ("S5", date(2026, 11, 6), "wsj.com"),
        ("S6", None, "ft.com"),
    ]


def test_catalog_ids_are_zero_padded_for_big_pools():
    pool = [_page(f"https://www.cnbc.com/2026/11/03/p{i}.html") for i in range(120)]

    catalog = build_catalog(pool, market="US", period_end=WEEK[1])

    assert [catalog[0].id, catalog[-1].id] == ["S001", "S120"]


def test_vn_catalog_reads_numeric_title_dates_day_first():
    pool = [_page("https://cafef.vn/tin.chn", title="VN-Index phiên 05/11/2026", day=2)]

    [entry] = build_catalog(pool, market="VN", period_end=WEEK[1])

    assert entry.session_date == date(2026, 11, 2)  # day-first numeric titles aren't parsed: Brave's date


# --- Prompt ----------------------------------------------------------------------------------------------------


def _prompt(market: str = "US", cadence: str = "weekly", period: tuple[date, date] = WEEK, pool=None) -> str:
    catalog = build_catalog(pool or _pool(), market=market, period_end=period[1])
    return build_curator_prompt(catalog, market=market, cadence=cadence, period_start=period[0], period_end=period[1])


def test_prompt_lists_every_page_with_id_date_publisher_and_a_short_excerpt():
    long_body = "Wall Street rallied. " * 100
    pool = [_page("https://www.cnbc.com/2026/11/02/a.html", title="Dow rises", body=long_body)]

    prompt = _prompt(pool=pool)

    assert "[S1] 2026-11-02 | cnbc.com | Dow rises | /2026/11/02/a.html | 2100 chars" in prompt
    assert long_body[:300].strip() in prompt
    assert long_body[:400] not in prompt


def test_weekly_prompt_asks_for_a_wrap_per_trading_day_and_states_limits():
    prompt = _prompt()

    assert "2026-11-02, 2026-11-03, 2026-11-04, 2026-11-05, 2026-11-06" in prompt
    assert "one session wrap" in prompt
    assert "week's move" in prompt
    assert "at least 2 stories on the week's main drivers" in prompt
    assert "at most 10 picks" in prompt
    assert "[CURATOR_JSON]" in prompt and "would_search" in prompt


def test_prompt_asks_for_driver_stories_and_a_publisher_mix_without_a_publisher_limit():
    prompt = _prompt()

    assert "Wraps say what happened; stories explain why." in prompt
    assert "Prefer a mix of publishers" in prompt
    assert "2 per publisher" not in prompt  # publisher mix is a goal, not a limit
    assert "Pages per publisher: cnbc.com 2, apnews.com 1" in prompt


def test_daily_prompt_is_about_the_single_session():
    prompt = _prompt(cadence="daily", period=(date(2026, 11, 6), date(2026, 11, 6)))

    assert "daily" in prompt
    assert "session of 2026-11-06" in prompt
    assert "at least 3 stories" in prompt
    assert "one session wrap" not in prompt


def test_market_briefs_name_each_markets_index_and_rules():
    assert "S&P 500" in _prompt("US")
    assert "OMX Helsinki" in _prompt("FI") and "Finnish" in _prompt("FI")
    vn = _prompt("VN")
    assert "VN-Index" in vn and "not wraps" in vn


def test_prompt_shows_no_rule_based_wrap_flag():
    assert "wrap?" not in _prompt()
    assert "is_session_wrap" not in _prompt()


# --- Parsing ---------------------------------------------------------------------------------------------------


def test_parse_reads_picks_and_would_search():
    raw = _answer(("S3", "wrap:2026-11-03"), ("S1", "story"), would_search=[{"missing": "Fri wrap", "query": "q"}])

    answer = parse_curator_output(raw, {"S1", "S3"})

    assert [(p.id, p.role, p.why) for p in answer.picks] == [
        ("S3", "wrap:2026-11-03", "reason"),
        ("S1", "story", "reason"),
    ]
    assert answer.would_search == [{"missing": "Fri wrap", "query": "q"}]
    assert answer.dropped == []


def test_parse_drops_unknown_and_repeated_ids():
    answer = parse_curator_output(_answer(("S1", "story"), ("S9", "story"), ("S1", "story")), {"S1"})

    assert [p.id for p in answer.picks] == ["S1"]
    assert answer.dropped == [("S9", "unknown_id"), ("S1", "duplicate")]


@pytest.mark.parametrize(
    "raw",
    [
        '{"picks": []}',  # no markers
        "[CURATOR_JSON]{not json[/CURATOR_JSON]",
        '[CURATOR_JSON]{"picks": "S1"}[/CURATOR_JSON]',
        '[CURATOR_JSON]["S1"][/CURATOR_JSON]',
    ],
)
def test_parse_rejects_malformed_answers(raw: str):
    with pytest.raises(CuratorError):
        parse_curator_output(raw, {"S1"})


def test_parse_ignores_malformed_would_search_and_pick_entries():
    raw = '[CURATOR_JSON]{"picks":[{"id":"S1"},"S2",{"role":"story"}],"would_search":"none"}[/CURATOR_JSON]'

    answer = parse_curator_output(raw, {"S1", "S2"})

    assert [(p.id, p.role, p.why) for p in answer.picks] == [("S1", "", "")]
    assert answer.would_search == []


# --- curate_sources --------------------------------------------------------------------------------------------


def test_curates_in_pick_order_with_one_call():
    agent = FakeAgent(_answer(("S3", "wrap:2026-11-03"), ("S1", "wrap:2026-11-02"), ("S5", "story")))

    result = _curate(agent)

    assert [c.url for c in result.candidates] == [
        "https://apnews.com/article/stocks-nov-3",
        "https://www.cnbc.com/2026/11/02/oil.html",
        "https://www.wsj.com/markets/fed-holds",
    ]
    assert len(agent.prompts) == 1
    assert result.audit["method"] == "curator"
    assert result.audit["model"] == "fake-curator"


def test_reasoning_is_off_so_it_cant_crowd_out_the_answer():
    agent = FakeAgent(_answer(("S1", "story"), ("S3", "story"), ("S5", "story")))

    _curate(agent)

    assert agent.options == [{"reasoning": {"enabled": False}, "max_tokens": 16_384}]


def test_audit_records_catalog_picks_would_search_and_the_raw_answer():
    gap = {"missing": "Thursday wrap", "query": "stocks close Nov 5 2026"}
    raw = _answer(("S3", "wrap:2026-11-03"), ("S1", "story"), ("S5", "story"), would_search=[gap])

    audit = _curate(FakeAgent(raw)).audit

    assert audit["catalog"][0] == {
        "id": "S1",
        "url": "https://www.cnbc.com/2026/11/02/oil.html",
        "session_date": "2026-11-02",
    }
    assert audit["picks"][0] == {
        "id": "S3",
        "url": "https://apnews.com/article/stocks-nov-3",
        "role": "wrap:2026-11-03",
        "why": "reason",
        "kept": True,
        "drop_reason": None,
    }
    assert audit["would_search"] == [gap]
    assert audit["raw_output"] == raw


def test_several_pages_from_one_publisher_are_kept():
    pool = [*_pool(), _page("https://www.cnbc.com/2026/11/05/third.html", title="Nasdaq slips", day=5)]
    agent = FakeAgent(_answer(("S1", "story"), ("S2", "story"), ("S5", "story")))  # all three are cnbc.com

    result = _curate(agent, pool)

    assert {c.url.split("/")[2] for c in result.candidates} == {"www.cnbc.com"}
    assert len(result.candidates) == 3


def test_unknown_ids_are_dropped_and_recorded_without_a_second_call():
    agent = FakeAgent(_answer(("S1", "story"), ("S3", "story"), ("S4", "story"), ("S9", "story")))

    result = _curate(agent)

    assert len(agent.prompts) == 1
    assert len(result.candidates) == 3
    assert {"id": "S9", "url": None, "role": "story", "why": "reason", "kept": False, "drop_reason": "unknown_id"} in (
        result.audit["picks"]
    )


def test_picks_past_top_k_are_dropped():
    agent = FakeAgent(_answer(("S1", "story"), ("S2", "story"), ("S3", "story"), ("S4", "story")))

    result = _curate(agent, top_k=3)

    assert len(result.candidates) == 3
    assert [p["drop_reason"] for p in result.audit["picks"] if not p["kept"]] == ["top_k"]


def test_fewer_than_three_valid_picks_is_a_failure():
    with pytest.raises(CuratorError, match="too_few_picks"):
        _curate(FakeAgent(_answer(("S1", "story"), ("S9", "story"))))


def test_a_curator_that_chooses_three_sources_is_respected():
    assert len(_curate(FakeAgent(_answer(("S1", "story"), ("S3", "story"), ("S5", "story")))).candidates) == 3


def test_agent_failure_is_a_curator_error():
    with pytest.raises(CuratorError, match="agent_error"):
        _curate(FakeAgent(RuntimeError("openrouter down")))


def test_malformed_answer_is_a_curator_error():
    with pytest.raises(CuratorError, match="missing_markers"):
        _curate(FakeAgent("no json here"))


def test_empty_pool_is_a_curator_error_without_calling_the_agent():
    agent = FakeAgent()

    with pytest.raises(CuratorError, match="empty_pool"):
        _curate(agent, pool=[])
    assert agent.prompts == []


# --- How the week ended: latest-day wrap guarantee -------------------------------------------------------------

WRAP_BODY = "The S&P 500 rose 1% and the Dow gained 200 points while the Nasdaq climbed. " * 8
WEEK_TOTAL_BODY = WRAP_BODY + " For the week, the S&P 500 fell 2%."


def _wrap_pool() -> list[Candidate]:
    return [
        _page("https://www.cnbc.com/2026/11/02/a.html", title="Monday stocks", body=WRAP_BODY, day=2),  # S1
        _page("https://www.reuters.com/markets/2026-11-03/b/", title="Tuesday stocks", body=WRAP_BODY, day=3),  # S2
        _page("https://www.wsj.com/2026/11/04/c", title="Wednesday stocks", body=WRAP_BODY, day=4),  # S3
        _page("https://www.cnbc.com/2026/11/06/d.html", title="Friday close", body=WRAP_BODY, day=6),  # S5
        _page(
            "https://apnews.com/2026/11/06/e", title="Friday week", body=WEEK_TOTAL_BODY, day=6
        ),  # S4 (apnews < cnbc)
    ]


def test_adds_the_week_total_friday_wrap_when_the_picks_lack_one(caplog):
    agent = FakeAgent(_answer(("S1", "wrap:2026-11-02"), ("S2", "wrap:2026-11-03"), ("S3", "story")))

    with caplog.at_level("INFO"):
        result = _curate(agent, _wrap_pool())

    assert [c.url for c in result.candidates][-1] == "https://apnews.com/2026/11/06/e"
    assert result.audit["added_latest_wrap"] == "S4"
    assert result.audit["displaced_by_latest_wrap"] == []
    [log] = [r for r in caplog.records if getattr(r, "event", None) == "recap.sources.selected"]
    assert log.fields["method"] == "curator"
    assert log.fields["sources"][-1] == "curator:latest_wrap apnews.com"


def test_a_friday_wrap_without_the_week_totals_is_not_enough_for_a_week():
    agent = FakeAgent(_answer(("S1", "story"), ("S2", "story"), ("S5", "wrap:2026-11-06")))

    result = _curate(agent, _wrap_pool())

    assert result.audit["added_latest_wrap"] == "S4"


def test_picks_that_already_say_how_the_week_ended_are_left_alone():
    agent = FakeAgent(_answer(("S1", "story"), ("S2", "story"), ("S4", "wrap:2026-11-06")))

    result = _curate(agent, _wrap_pool())

    assert result.audit["added_latest_wrap"] is None
    assert [c.url for c in result.candidates][-1] == "https://apnews.com/2026/11/06/e"


def test_the_added_wrap_displaces_the_lowest_priority_pick_at_the_limit():
    agent = FakeAgent(_answer(("S1", "story"), ("S2", "story"), ("S3", "story")))

    result = _curate(agent, _wrap_pool(), top_k=3)

    assert result.audit["added_latest_wrap"] == "S4"
    assert result.audit["displaced_by_latest_wrap"] == ["S3"]
    assert len(result.candidates) == 3


def test_daily_recaps_need_any_wrap_of_the_session():
    daily = (date(2026, 11, 6), date(2026, 11, 6))
    pool = [
        _page("https://www.cnbc.com/2026/11/06/jobs.html", title="Jobs report", day=6),  # S3
        _page("https://www.reuters.com/markets/2026-11-06/fed/", title="Fed odds", day=6),  # S4
        _page("https://www.wsj.com/2026/11/06/chips", title="Chips rally", day=6),  # S5
        _page("https://www.cnbc.com/2026/11/06/close.html", title="Friday close", body=WRAP_BODY, day=6),  # S2
        _page("https://apnews.com/2026/11/06/wrap", title="Stocks Friday", body=WRAP_BODY, day=6),  # S1
    ]
    agent = FakeAgent(_answer(("S3", "story"), ("S4", "story"), ("S5", "story")))

    result = _curate(agent, pool, period_start=daily[0], period_end=daily[1], cadence="daily")

    assert result.audit["added_latest_wrap"] == "S1"  # picks already use cnbc.com: the less-used publisher wins


def test_vn_has_no_wrap_rule_so_nothing_is_added():
    pool = [
        _page(f"https://{site}/2026/11/0{d}/tin", title="VN-Index", day=d)
        for site, d in (("cafef.vn", 2), ("vietstock.vn", 3), ("vneconomy.vn", 4), ("vnexpress.net", 6))
    ]
    agent = FakeAgent(_answer(("S1", "story"), ("S2", "story"), ("S3", "story")))

    result = _curate(agent, pool, market="VN")

    assert result.audit["added_latest_wrap"] is None


# --- Session-date window ----------------------------------------------------------------------------------------


def test_pages_dated_outside_the_period_never_reach_the_curator():
    stale = _page("https://www.cnbc.com/2026/10/15/old-wrap.html", title="Stocks fall", day=3)  # Brave date in window
    pool = [*_pool(), stale]
    agent = FakeAgent(_answer(("S1", "story"), ("S3", "story"), ("S5", "story")))

    result = _curate(agent, pool)

    assert "old-wrap" not in agent.prompts[0]
    assert result.audit["out_of_window"] == ["https://www.cnbc.com/2026/10/15/old-wrap.html"]
    assert len(result.audit["catalog"]) == len(_pool())


def test_undated_pages_and_one_day_of_slack_stay_in():
    undated = Candidate(title="Markets", url="https://www.ft.com/markets", raw_content="x", provider="brave")
    sunday_after = _page("https://www.wsj.com/2026/11/07/weekend-wrap", title="Week ahead", day=7)
    agent = FakeAgent(_answer(("S1", "story"), ("S3", "story"), ("S5", "story")))

    result = _curate(agent, [*_pool(), undated, sunday_after])

    assert result.audit["out_of_window"] == []
    assert len(result.audit["catalog"]) == len(_pool()) + 2


def test_a_pool_entirely_out_of_window_is_a_curator_error_without_calling_the_agent():
    agent = FakeAgent()
    pool = [_page("https://www.cnbc.com/2026/10/15/old.html", day=3)]

    with pytest.raises(CuratorError, match="empty_pool"):
        _curate(agent, pool)
    assert agent.prompts == []
