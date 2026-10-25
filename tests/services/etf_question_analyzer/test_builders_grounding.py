"""ETF context builders: `grounding` replaces the source/search instructions; grounding=None keeps them (v1)."""

from __future__ import annotations

from typing import Callable

import pytest

from services.etf_question_analyzer.context_builders import (
    BasicETFBuilder,
    ComparisonContextBuilderInput,
    ComparisonETFBuilder,
    DetailedETFBuilder,
    ETFContextBuilderInput,
    ETFPromptComponents,
    NoneETFBuilder,
)
from tests.services.etf_question_analyzer.etf_fakes import make_etf

GROUNDING = "**Grounding rules (test):** use the data blocks only."


def _etf_input(etf=None, *, deep: bool = False, grounding: str | None = None) -> ETFContextBuilderInput:
    kwargs = {"grounding": grounding} if grounding is not None else {}
    return ETFContextBuilderInput(
        ticker="SXR8",
        question="What is the TER?",
        etf_data=etf,
        use_google_search=True,
        deep_analysis=deep,
        **kwargs,
    )


def _comparison_input(*, short: bool, grounding: str | None = None) -> ComparisonContextBuilderInput:
    kwargs = {"grounding": grounding} if grounding is not None else {}
    etfs = [make_etf("SXR8"), make_etf("VUSA", name="Vanguard S&P 500 UCITS ETF", fund_provider="Vanguard")]
    return ComparisonContextBuilderInput(
        tickers=["SXR8", "VUSA"],
        question="Compare SXR8 vs VUSA",
        etf_data_list=etfs,
        use_google_search=True,
        short_analysis=short,
        **kwargs,
    )


def _empty_etf():
    return make_etf(holdings=[], sector_allocation=[], country_allocation=[])


# name -> builder call with grounding=None (the v1 path).
V1_CASES: dict[str, Callable[[], str]] = {
    "none": lambda: NoneETFBuilder().build(_etf_input()),
    "basic_missing": lambda: BasicETFBuilder().build(_etf_input(None)),
    "detailed_short_empty": lambda: DetailedETFBuilder().build(_etf_input(_empty_etf())),
}


_SOURCE_MARKER = "CRITICAL - Source Citation"
_SEARCH_HINTS = ("Google Search", "search online", "USE SEARCH WISELY")


GROUNDED_CASES: dict[str, Callable[[], str]] = {
    "none": lambda: NoneETFBuilder().build(_etf_input(grounding=GROUNDING)),
    "basic_full": lambda: BasicETFBuilder().build(_etf_input(make_etf(), grounding=GROUNDING)),
    "basic_missing": lambda: BasicETFBuilder().build(_etf_input(None, grounding=GROUNDING)),
    "detailed_short_empty": lambda: DetailedETFBuilder().build(_etf_input(_empty_etf(), grounding=GROUNDING)),
    "detailed_deep_empty": lambda: DetailedETFBuilder().build(_etf_input(_empty_etf(), deep=True, grounding=GROUNDING)),
    "comparison_short": lambda: ComparisonETFBuilder().build(_comparison_input(short=True, grounding=GROUNDING)),
    "comparison_deep": lambda: ComparisonETFBuilder().build(_comparison_input(short=False, grounding=GROUNDING)),
}


@pytest.mark.parametrize("name", sorted(GROUNDED_CASES))
def test_grounding_replaces_source_instructions_and_search_hints(name: str) -> None:
    prompt = GROUNDED_CASES[name]()
    assert prompt.count(GROUNDING) == 1
    assert _SOURCE_MARKER not in prompt
    for hint in _SEARCH_HINTS:
        assert hint not in prompt, hint


def test_v1_outputs_still_have_source_instructions_and_search_hints() -> None:
    assert _SOURCE_MARKER in V1_CASES["none"]()
    assert "Use Google Search if needed" in V1_CASES["none"]()
    assert "Use Google Search to supplement" in V1_CASES["basic_missing"]()
    assert "search online if needed" in V1_CASES["detailed_short_empty"]()


def test_grounded_detailed_still_reports_missing_data() -> None:
    prompt = DetailedETFBuilder().build(_etf_input(_empty_etf(), grounding=GROUNDING))
    assert "Holdings data unavailable" in prompt
    assert "Sector allocation unavailable" in prompt
    assert "Country allocation unavailable" in prompt


def test_grounded_basic_still_flags_incomplete_data() -> None:
    assert "Some ETF data may be incomplete" in BasicETFBuilder().build(_etf_input(None, grounding=GROUNDING))


def test_source_instructions_component_is_unchanged() -> None:
    assert _SOURCE_MARKER in ETFPromptComponents.source_instructions()
