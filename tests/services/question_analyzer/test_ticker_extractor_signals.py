"""Comparison-signal pre-check and contextual-preprocess gating in StockTickerExtractor."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.question_analyzer.ticker_extractor import (
    StockTickerExtractor,
    _has_comparison_signals,
    _has_contextual_reference,
)


@pytest.mark.parametrize(
    "question, expected",
    [
        # False positives fixed: "or sell" (IGNORECASE), "P/E" (1-letter tokens), finance acronyms
        ("Should I buy or sell Apple stock now?", False),
        ("What is a P/E ratio?", False),
        ("Is EPS or ROE more important?", False),
        ("What is the EV/EBITDA of NVDA?", False),
        ("Is the CEO or CFO selling shares?", False),
        ("Is it a buy or not?", False),
        ("What is Apple's TTM EPS?", False),
        ("Is EPS or P/E a better metric?", False),
        ("Should I buy now or I wait?", False),
        ("Should I hold or sell Tesla?", False),
        # Capitalized common words after "or" are not companies; neither is a leading "Or"
        ("Should I buy or Sell Apple?", False),
        ("Is it overvalued or Undervalued?", False),
        ("Or should I wait?", False),
        # Lowercase pairs that aren't companies
        ("should i hold or sell?", False),
        ("stocks or bonds?", False),
        ("Is annual or quarterly revenue better?", False),
        ("q1 or q2?", False),
        # Real comparisons still detected
        ("AAPL or MSFT?", True),
        ("Buy AAPL or MSFT", True),
        ("Tesla or Ford?", True),
        ("Should I buy Tesla or Ford stock?", True),
        ("Is Apple or Microsoft the better buy?", True),
        ("TSLA or F", True),
        ("VZ or T?", True),
        ("AAPL OR MSFT", True),
        # Lowercase tickers / names (chat and mobile input)
        ("tesla or ford?", True),
        ("should i buy nvda or amd", True),
        ("aapl or msft?", True),
        ("Tesla or ford?", True),
        ("Compare Apple vs Microsoft", True),
        ("Is Tesla overvalued compared to Ford?", True),
        ("AAPL MSFT revenue", True),
        ("AAPL/MSFT ratio", True),
        ("How does it compare to GOOGL?", True),
    ],
)
def test_has_comparison_signals(question, expected):
    assert _has_comparison_signals(question) is expected


@pytest.mark.parametrize(
    "question, expected",
    [
        ("Compare this stock with MSFT", True),
        ("How does it compare to GOOGL?", True),
        ("Is the company cheaper than MSFT?", True),
        ("Compare this one vs NVDA", True),
        ("Compare Apple vs Microsoft", False),
        ("AAPL or MSFT?", False),
        ("Is Tesla overvalued compared to Ford?", False),
    ],
)
def test_has_contextual_reference(question, expected):
    assert _has_contextual_reference(question) is expected


@pytest.fixture
def extractor():
    with (
        patch("services.question_analyzer.ticker_extractor.CompanyConnector") as connector_cls,
        patch("services.question_analyzer.ticker_extractor.MultiAgent") as agent_cls,
    ):
        connector = MagicMock()
        connector.get_by_ticker.return_value = MagicMock()
        connector_cls.return_value = connector
        agent = MagicMock()
        agent.generate_content.return_value = iter(['{"tickers": ["AAPL", "MSFT"]}'])
        agent_cls.return_value = agent
        instance = StockTickerExtractor()
        instance._preprocess_question_with_context = AsyncMock(side_effect=lambda q, _t: q)
        yield instance


@pytest.mark.asyncio
async def test_no_signals_skips_all_llm_calls(extractor):
    assert await extractor.extract_tickers("Should I buy or sell Apple stock now?", current_ticker="AAPL") == []
    extractor._preprocess_question_with_context.assert_not_called()
    extractor.agent.generate_content.assert_not_called()


@pytest.mark.asyncio
async def test_comparison_without_contextual_reference_skips_preprocess(extractor):
    tickers = await extractor.extract_tickers("Compare Apple vs Microsoft", current_ticker="AAPL")

    assert tickers == ["AAPL", "MSFT"]
    extractor._preprocess_question_with_context.assert_not_called()


@pytest.mark.asyncio
async def test_comparison_with_contextual_reference_uses_rewritten_question(extractor):
    extractor._preprocess_question_with_context.side_effect = lambda _q, _t: "How does AAPL compare to GOOGL?"

    tickers = await extractor.extract_tickers("How does it compare to GOOGL?", current_ticker="AAPL")

    extractor._preprocess_question_with_context.assert_awaited_once_with("How does it compare to GOOGL?", "AAPL")
    assert tickers == ["AAPL", "GOOGL"]
    extractor.agent.generate_content.assert_not_called()


@pytest.mark.asyncio
async def test_contextual_reference_without_current_ticker_skips_preprocess(extractor):
    await extractor.extract_tickers("How does it compare to GOOGL?", current_ticker=None)

    extractor._preprocess_question_with_context.assert_not_called()
