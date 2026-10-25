"""QuestionRouter: prompt, strict parsing, fast-path overrides, fallback (None) on failure. Fake LLM only."""

from __future__ import annotations

import json
import time

import pytest

from core.financial_statement_type import FinancialStatementType
from services.question_analyzer.router import (
    QuestionRouter,
    apply_fast_paths,
    build_router_prompt,
    parse_routing_response,
)
from services.question_analyzer.types import FinancialDataRequirement, FinancialPeriodRequirement, QuestionType


def _payload(**overrides) -> dict:
    payload = {
        "standalone_question": "What was Nvidia's data center revenue last quarter?",
        "question_type": "company-specific-finance",
        "comparison_tickers": [],
        "data_requirement": "detailed",
        "period_requirement": {
            "period_type": "quarterly",
            "specific_years": None,
            "specific_quarters": ["latest"],
            "num_periods": 1,
        },
        "relevant_statements": ["income_statement"],
        "use_web_search": True,
        "reason_code": "db_metric_missing",
        "search_queries": ["Nvidia data center revenue Q3 2026", "Nvidia quarterly results data center segment"],
    }
    payload.update(overrides)
    return payload


def _parse(payload: dict | str, ticker: str = "NVDA", question: str = "q"):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return parse_routing_response(raw, question=question, ticker=ticker, model_name="m")


def test_parse_full_payload():
    result = _parse(_payload())
    assert result.question_type == QuestionType.COMPANY_SPECIFIC_FINANCE.value
    assert result.data_requirement == FinancialDataRequirement.DETAILED
    assert result.period_requirement == FinancialPeriodRequirement(
        period_type="quarterly", specific_quarters=["latest"], num_periods=1
    )
    assert result.relevant_statements == [FinancialStatementType.INCOME_STATEMENT]
    assert result.search_decision.use_google_search is True
    assert result.search_decision.reason_code == "db_metric_missing"
    assert result.search_decision.decision_model == "router:m"
    assert result.search_decision.decision_fallback == "none"
    assert result.search_queries == [
        "Nvidia data center revenue Q3 2026",
        "Nvidia quarterly results data center segment",
    ]
    assert result.standalone_question == "What was Nvidia's data center revenue last quarter?"


def test_parse_accepts_markdown_fence_and_prose():
    raw = "Here you go:\n```json\n" + json.dumps(_payload()) + "\n```"
    assert _parse(raw).question_type == QuestionType.COMPANY_SPECIFIC_FINANCE.value


@pytest.mark.parametrize(
    "overrides",
    [
        {"question_type": "stock-question"},
        {"question_type": None},
        {"data_requirement": "url_context"},
        {"data_requirement": "everything"},
        {"use_web_search": "yes"},
        {"reason_code": ""},
        {"search_queries": []},
        {"search_queries": "Nvidia revenue"},
        {"search_queries": [1, 2]},
        {"standalone_question": ""},
        {"comparison_tickers": "NVDA,AMD"},
        {"period_requirement": {"period_type": "monthly"}},
        {"period_requirement": {"period_type": "annual", "specific_years": ["2024"]}},
        {"period_requirement": {"period_type": "annual", "num_periods": "3"}},
    ],
)
def test_parse_rejects_invalid_fields(overrides):
    with pytest.raises(ValueError):
        _parse(_payload(**overrides))


def test_parse_rejects_non_json():
    with pytest.raises(ValueError):
        _parse("company-specific-finance")
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


def test_detailed_statements_follow_classifier_contract():
    all_ordered = list(FinancialStatementType.all_ordered())
    assert _parse(_payload(relevant_statements=None)).relevant_statements == all_ordered
    assert _parse(_payload(relevant_statements=["bogus"])).relevant_statements == all_ordered
    assert (
        _parse(_payload(relevant_statements=["cash_flow", "balance_sheet", "income_statement"])).relevant_statements
        == all_ordered
    )
    basic = _parse(_payload(data_requirement="basic", relevant_statements=["income_statement"]))
    assert basic.relevant_statements is None
    assert basic.period_requirement is None


def test_missing_period_uses_classifier_fallback():
    assert _parse(_payload(period_requirement=None)).period_requirement == FinancialPeriodRequirement(
        period_type="annual", num_periods=3
    )
    summary = _parse(_payload(data_requirement="quarterly_summary", period_requirement=None))
    assert summary.period_requirement == FinancialPeriodRequirement(period_type="quarterly", num_periods=1)


def test_company_specific_without_ticker_becomes_general_finance():
    assert _parse(_payload(), ticker="none").question_type == QuestionType.GENERAL_FINANCE.value


def test_comparison_tickers_are_normalised():
    result = _parse(
        _payload(question_type="company-comparison", comparison_tickers=["nvda", "AMD", "AMD", "not a ticker"])
    )
    assert result.comparison_tickers == ["NVDA", "AMD"]


def test_prompt_includes_conversation_db_context_and_ticker():
    prompt = build_router_prompt(
        question="How does that compare with AMD?",
        ticker="NVDA",
        conversation_messages=[
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "What was Nvidia's data center revenue last quarter?"},
            {"role": "assistant", "content": "x" * 1000},
        ],
        available_periods={"annual": [2025, 2024], "quarterly": ["7/31/2026"]},
        available_metrics=["Revenue", "Net Income"],
    )
    assert "LATEST question: How does that compare with AMD?" in prompt
    assert "Ticker the user is viewing: NVDA" in prompt
    assert "USER: What was Nvidia's data center revenue last quarter?" in prompt
    assert "old question" in prompt  # last 2 turns = 4 messages
    assert "x" * 301 not in prompt  # assistant turns are truncated
    assert "Available metrics (ONLY these): Revenue, Net Income" in prompt
    assert "Annual statements: [2025, 2024]" in prompt


def test_prompt_keeps_only_last_two_turns():
    messages = [{"role": "user", "content": f"turn-{i}"} for i in range(6)]
    prompt = build_router_prompt(question="q", ticker="AAPL", conversation_messages=messages)
    assert "turn-1" not in prompt
    assert "turn-2" in prompt and "turn-5" in prompt


def test_prompt_without_ticker_or_history():
    prompt = build_router_prompt(question="What is a P/E ratio?", ticker="none")
    assert "Ticker the user is viewing: none" in prompt
    assert "Recent conversation" not in prompt
    assert "Financial data in our database" not in prompt


def test_fast_path_static_fact_turns_search_off():
    result = apply_fast_paths(_parse(_payload()), question="Who founded Nvidia?")
    assert result.search_decision.use_google_search is False
    assert result.search_decision.reason_code == "stable_concept"
    assert result.search_queries == []


def test_fast_path_forced_search_reason_wins():
    routed = _parse(_payload(use_web_search=False, reason_code="db_data_sufficient", search_queries=[]))
    result = apply_fast_paths(routed, question="Who founded Nvidia?", force_google_search_reason="sec_url")
    assert result.search_decision.use_google_search is True
    assert result.search_decision.reason_code == "sec_url"
    assert result.search_queries == [routed.standalone_question]


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Summarize the latest 10-Q", FinancialDataRequirement.QUARTERLY_SUMMARY),
        ("What does the annual report say about risks?", FinancialDataRequirement.ANNUAL_SUMMARY),
    ],
)
def test_fast_path_report_keywords_override_data_requirement(question, expected):
    result = apply_fast_paths(_parse(_payload()), question=question)
    assert result.data_requirement == expected
    assert result.period_requirement is None
    assert result.relevant_statements is None


@pytest.mark.asyncio
async def test_route_returns_parsed_result_and_passes_prompt():
    prompts: list[str] = []

    def llm(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps(_payload())

    result = await QuestionRouter(llm=llm).route(question="data center revenue?", ticker="NVDA")
    assert result is not None
    assert result.data_requirement == FinancialDataRequirement.DETAILED
    assert len(prompts) == 1 and "LATEST question: data center revenue?" in prompts[0]


@pytest.mark.asyncio
async def test_route_returns_none_on_invalid_output():
    assert await QuestionRouter(llm=lambda _p: "not json").route(question="q", ticker="NVDA") is None


@pytest.mark.asyncio
async def test_route_returns_none_on_llm_error():
    def llm(_prompt: str) -> str:
        raise RuntimeError("openrouter down")

    assert await QuestionRouter(llm=llm).route(question="q", ticker="NVDA") is None


@pytest.mark.asyncio
async def test_route_returns_none_on_timeout():
    def slow_llm(_prompt: str) -> str:
        time.sleep(0.3)
        return json.dumps(_payload())

    start = time.perf_counter()
    assert await QuestionRouter(llm=slow_llm, timeout_seconds=0.05).route(question="q", ticker="NVDA") is None
    assert time.perf_counter() - start < 0.25
