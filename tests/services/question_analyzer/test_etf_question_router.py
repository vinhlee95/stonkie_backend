"""QuestionRouter ETF mode: prompt, strict parsing, fast paths, default routing, None on failure. Fake LLM only."""

from __future__ import annotations

import json
import time

import pytest

from services.etf_question_analyzer.types import ETFDataRequirement, ETFQuestionType
from services.question_analyzer.router import (
    MAX_QUERY_CHARS,
    MAX_QUERY_WORDS,
    ETFRoutingResult,
    QuestionRouter,
    apply_etf_fast_paths,
    build_etf_router_prompt,
    default_etf_routing,
    parse_etf_routing_response,
    trim_search_query,
)


def _payload(**overrides) -> dict:
    payload = {
        "standalone_question": "What is the TER of the iShares Core S&P 500 UCITS ETF?",
        "question_type": "etf_overview",
        "data_requirement": "basic",
        "use_web_search": True,
        "reason_code": "latest_info",
        "search_queries": ["iShares Core S&P 500 UCITS ETF TER 2026", "iShares Core S&P 500 UCITS ETF factsheet"],
    }
    payload.update(overrides)
    return payload


def _parse(payload: dict | str, ticker: str = "SXR8", question: str = "q") -> ETFRoutingResult:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return parse_etf_routing_response(raw, question=question, ticker=ticker, model_name="m")


def test_parse_full_payload():
    result = _parse(_payload())
    assert result.question_type == ETFQuestionType.ETF_OVERVIEW
    assert result.data_requirement == ETFDataRequirement.BASIC
    assert result.search_decision.use_google_search is True
    assert result.search_decision.reason_code == "latest_info"
    assert result.search_decision.decision_model == "router:m"
    assert result.search_decision.decision_fallback == "none"
    assert result.search_queries == [
        "iShares Core S&P 500 UCITS ETF TER 2026",
        "iShares Core S&P 500 UCITS ETF factsheet",
    ]
    assert result.standalone_question == "What is the TER of the iShares Core S&P 500 UCITS ETF?"


def test_parse_accepts_markdown_fence_and_prose():
    raw = "Sure:\n```json\n" + json.dumps(_payload()) + "\n```"
    assert _parse(raw).question_type == ETFQuestionType.ETF_OVERVIEW


@pytest.mark.parametrize(
    "overrides",
    [
        {"question_type": "company-general"},
        {"question_type": "etf_comparison"},
        {"question_type": None},
        {"data_requirement": "quarterly_summary"},
        {"data_requirement": None},
        {"use_web_search": "yes"},
        {"reason_code": ""},
        {"reason_code": None},
        {"search_queries": []},
        {"search_queries": "iShares TER"},
        {"search_queries": [1, 2]},
        {"standalone_question": ""},
        {"standalone_question": None},
    ],
)
def test_parse_rejects_invalid_fields(overrides):
    with pytest.raises(ValueError):
        _parse(_payload(**overrides))


def test_parse_rejects_non_json():
    with pytest.raises(ValueError):
        _parse("etf_overview")
    with pytest.raises(ValueError):
        _parse('{"question_type": ')


def test_unknown_reason_code_maps_to_other():
    assert _parse(_payload(reason_code="because")).search_decision.reason_code == "other"


def test_search_off_drops_queries():
    result = _parse(_payload(use_web_search=False, reason_code="db_data_sufficient", search_queries=["x"]))
    assert result.search_decision.use_google_search is False
    assert result.search_queries == []


def test_queries_are_capped_and_deduped():
    result = _parse(_payload(search_queries=["a  b", "a b", "c", "d", "e"]))
    assert result.search_queries == ["a b", "c", "d"]


@pytest.mark.parametrize(
    ("question_type", "expected"),
    [
        ("etf_overview", ETFDataRequirement.BASIC),
        ("etf_detailed_analysis", ETFDataRequirement.DETAILED),
    ],
)
def test_fund_specific_type_never_gets_no_data(question_type, expected):
    assert _parse(_payload(question_type=question_type, data_requirement="none")).data_requirement == expected


def test_detailed_requirement_is_kept():
    result = _parse(_payload(question_type="etf_detailed_analysis", data_requirement="detailed"))
    assert result.question_type == ETFQuestionType.ETF_DETAILED_ANALYSIS
    assert result.data_requirement == ETFDataRequirement.DETAILED


def test_general_etf_keeps_its_requirement():
    result = _parse(_payload(question_type="general_etf", data_requirement="none"))
    assert result.question_type == ETFQuestionType.GENERAL_ETF
    assert result.data_requirement == ETFDataRequirement.NONE


def test_fund_specific_without_ticker_becomes_general_etf():
    result = _parse(_payload(question_type="etf_detailed_analysis", data_requirement="detailed"), ticker="none")
    assert result.question_type == ETFQuestionType.GENERAL_ETF
    assert result.data_requirement == ETFDataRequirement.NONE


def test_prompt_includes_db_context_conversation_and_ticker():
    prompt = build_etf_router_prompt(
        question="And its top holdings?",
        ticker="SXR8",
        conversation_messages=[
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "What is the TER of SXR8?"},
            {"role": "assistant", "content": "x" * 1000},
        ],
        etf_name="iShares Core S&P 500 UCITS ETF",
        fund_provider="iShares",
        index_tracked="S&P 500",
    )
    assert "LATEST question: And its top holdings?" in prompt
    assert "ETF the user is viewing: SXR8" in prompt
    assert "Name: iShares Core S&P 500 UCITS ETF" in prompt
    assert "Provider: iShares" in prompt
    assert "Index tracked: S&P 500" in prompt
    assert "USER: What is the TER of SXR8?" in prompt
    assert "old question" in prompt  # last 2 turns = 4 messages
    assert "x" * 301 not in prompt  # assistant turns are truncated
    for value in ("general_etf", "etf_overview", "etf_detailed_analysis", "standalone_question", "search_queries"):
        assert value in prompt
    assert "fund NAME" in prompt


def test_prompt_keeps_only_last_two_turns():
    messages = [{"role": "user", "content": f"turn-{i}"} for i in range(6)]
    prompt = build_etf_router_prompt(question="q", ticker="SXR8", conversation_messages=messages)
    assert "turn-1" not in prompt
    assert "turn-2" in prompt and "turn-5" in prompt


def test_prompt_without_db_row_or_history():
    prompt = build_etf_router_prompt(question="What is an ETF?", ticker="none")
    assert "ETF the user is viewing: none" in prompt
    assert "Recent conversation" not in prompt
    assert "Name:" not in prompt


def test_prompt_with_ticker_but_no_db_row_says_so():
    prompt = build_etf_router_prompt(question="What is the TER?", ticker="ABCD")
    assert "Our database has no data for ABCD." in prompt


def test_fast_path_static_fact_turns_search_off():
    result = apply_etf_fast_paths(_parse(_payload()), question="When was SXR8 founded?")
    assert result.search_decision.use_google_search is False
    assert result.search_decision.reason_code == "stable_concept"
    assert result.search_decision.decision_model == "keyword_prefilter"
    assert result.search_queries == []


def test_fast_path_keeps_router_decision_otherwise():
    routed = _parse(_payload())
    assert apply_etf_fast_paths(routed, question="What is the TER?") == routed


def test_default_routing_with_ticker_is_overview_basic_search_on():
    result = default_etf_routing(question="Tell me about it", ticker="SXR8")
    assert result.question_type == ETFQuestionType.ETF_OVERVIEW
    assert result.data_requirement == ETFDataRequirement.BASIC
    assert result.search_decision.use_google_search is True
    assert result.search_queries == []
    assert result.standalone_question == "Tell me about it"


@pytest.mark.parametrize("ticker", ["", "none", "UNDEFINED"])
def test_default_routing_without_ticker_is_general_none(ticker):
    result = default_etf_routing(question="What is an ETF?", ticker=ticker)
    assert result.question_type == ETFQuestionType.GENERAL_ETF
    assert result.data_requirement == ETFDataRequirement.NONE
    assert result.search_decision.use_google_search is True


@pytest.mark.asyncio
async def test_route_etf_returns_parsed_result_and_passes_prompt():
    prompts: list[str] = []

    def llm(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps(_payload())

    result = await QuestionRouter(llm=llm).route_etf(
        question="What is the TER?", ticker="SXR8", etf_name="iShares Core S&P 500 UCITS ETF"
    )
    assert result is not None
    assert result.question_type == ETFQuestionType.ETF_OVERVIEW
    assert len(prompts) == 1
    assert "LATEST question: What is the TER?" in prompts[0]
    assert "Name: iShares Core S&P 500 UCITS ETF" in prompts[0]


@pytest.mark.asyncio
async def test_route_etf_returns_none_on_invalid_output():
    assert await QuestionRouter(llm=lambda _p: "not json").route_etf(question="q", ticker="SXR8") is None


@pytest.mark.asyncio
async def test_route_etf_returns_none_on_llm_error():
    def llm(_prompt: str) -> str:
        raise RuntimeError("openrouter down")

    assert await QuestionRouter(llm=llm).route_etf(question="q", ticker="SXR8") is None


@pytest.mark.asyncio
async def test_route_etf_returns_none_on_timeout():
    def slow_llm(_prompt: str) -> str:
        time.sleep(0.3)
        return json.dumps(_payload())

    start = time.perf_counter()
    assert await QuestionRouter(llm=slow_llm, timeout_seconds=0.05).route_etf(question="q", ticker="SXR8") is None
    assert time.perf_counter() - start < 0.25


def test_trim_search_query_caps_chars_and_words():
    assert trim_search_query("  a   b \n c ") == "a b c"
    long_words = " ".join(f"w{i}" for i in range(100))
    assert len(trim_search_query(long_words).split()) == MAX_QUERY_WORDS
    long_chars = "x" * 1000
    assert trim_search_query(long_chars) == "x" * MAX_QUERY_CHARS


def test_router_queries_use_the_same_trimming():
    long_query = " ".join(["word"] * 120)
    result = _parse(_payload(search_queries=[long_query]))
    assert result.search_queries == [trim_search_query(long_query)]
