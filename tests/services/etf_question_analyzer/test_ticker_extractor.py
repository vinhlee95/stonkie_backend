"""ETFTickerExtractor: acronym-aware comparison gate and blocking work kept off the event loop.

Fake connector and agent only: no LLM or DB calls.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from services.etf_analyzer_v2 import ETFAnalyzerV2
from services.etf_question_analyzer.ticker_extractor import ETFTickerExtractor, _has_comparison_signals
from services.question_analyzer.router import QuestionRouter
from tests.services.etf_question_analyzer.etf_fakes import make_etf

KNOWN = {"SXR8": make_etf("SXR8"), "VUSA": make_etf("VUSA", name="Vanguard S&P 500 UCITS ETF")}


class _Connector:
    def get_by_ticker(self, ticker: str):
        return KNOWN.get(ticker)

    def get_all(self):
        return list(KNOWN.values())


class _Agent:
    """Sync fake LLM: sleeps (blocking) then echoes back a rewritten question."""

    def __init__(self, delay: float = 0.0, response: str = "Compare SXR8 with VUSA"):
        self.delay = delay
        self.response = response
        self.prompts: list[str] = []

    def generate_content(self, prompt: str, **_kwargs):
        self.prompts.append(prompt)
        time.sleep(self.delay)
        yield self.response


@pytest.mark.parametrize(
    "question",
    [
        "What is the TER of this ETF?",
        "How much does SXR8 hold in US stocks?",
        "Is the MSCI World UCITS ETF ACC or DIST?",
        "Is TER or NAV more important for UCITS ETFs?",
        "Should I buy or sell this ETF?",
        "What is the AUM in USD and EUR?",
        "Does it track the FTSE All-World or the MSCI ACWI index?",
        "Should I buy or sell SXR8?",
        "What is the TER or the AUM of SXR8?",
        "Is SXR8 accumulating or distributing?",
    ],
)
def test_acronyms_are_not_comparison_signals(question):
    assert _has_comparison_signals(question) is False


@pytest.mark.parametrize(
    "question",
    [
        "SXR8 vs VUSA",
        "Compare this with VUSA",
        "Should I pick SXR8 or VUSA?",
        "SXR8 CSPX which one has the lower TER?",
        "VWCE or the Vanguard FTSE All-World?",
        "SXR8 or the iShares Core MSCI World?",
        "Should I get VWCE or Vanguard's S&P 500 fund?",
    ],
)
def test_real_comparisons_still_pass_the_gate(question):
    assert _has_comparison_signals(question) is True


@pytest.mark.asyncio
async def test_non_comparison_question_with_acronyms_skips_the_llm():
    agent = _Agent()
    extractor = ETFTickerExtractor(connector=_Connector(), agent=agent)
    assert await extractor.extract_tickers("What is the TER of this ETF in USD?", current_ticker="SXR8") == []
    assert agent.prompts == []


@pytest.mark.asyncio
async def test_comparison_still_resolves_tickers():
    agent = _Agent(response="Compare SXR8 with VUSA")
    extractor = ETFTickerExtractor(connector=_Connector(), agent=agent)
    assert await extractor.extract_tickers("Compare this with VUSA", current_ticker="SXR8") == ["SXR8", "VUSA"]
    assert len(agent.prompts) == 1


@pytest.mark.asyncio
async def test_slow_llm_does_not_block_the_event_loop():
    extractor = ETFTickerExtractor(connector=_Connector(), agent=_Agent(delay=0.3))
    ticks = 0
    done = asyncio.Event()

    async def ticker():
        nonlocal ticks
        while not done.is_set():
            ticks += 1
            await asyncio.sleep(0.01)

    tick_task = asyncio.ensure_future(ticker())
    try:
        await extractor.extract_tickers("Compare this with VUSA", current_ticker="SXR8")
    finally:
        done.set()
        await tick_task
    assert ticks >= 10


@pytest.mark.asyncio
async def test_router_result_survives_slow_extraction():
    payload = {
        "standalone_question": "What is the TER of SXR8?",
        "question_type": "etf_detailed_analysis",
        "data_requirement": "detailed",
        "use_web_search": False,
        "reason_code": "db_data_sufficient",
        "search_queries": [],
    }
    router = QuestionRouter(llm=lambda _prompt: json.dumps(payload), timeout_seconds=0.2)
    extractor = ETFTickerExtractor(connector=_Connector(), agent=_Agent(delay=0.5, response="Compare SXR8 with X"))
    analyzer = ETFAnalyzerV2(router=router, ticker_extractor=extractor)

    routing, tickers = await analyzer._route(
        question="Compare this with the market", ticker="SXR8", conversation_messages=None, etf=make_etf()
    )

    assert routing.search_decision.decision_model.startswith("router:")  # not the default routing
    assert routing.search_decision.use_google_search is False
    assert tickers == []
