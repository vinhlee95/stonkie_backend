"""ETF context builders: `grounding` replaces any source-citation or search instructions."""

from __future__ import annotations

from datetime import date
from typing import Callable

import pytest

from services.etf_question_analyzer.context_builders import (
    BasicETFBuilder,
    ComparisonContextBuilderInput,
    ComparisonETFBuilder,
    DetailedETFBuilder,
    ETFContextBuilderInput,
    NoneETFBuilder,
)
from services.etf_question_analyzer.context_builders import components as components_module
from tests.services.etf_question_analyzer.etf_fakes import make_etf

GROUNDING = "**Grounding rules (test):** use the data blocks only."


class _FixedDate(date):
    @classmethod
    def today(cls) -> "_FixedDate":
        return cls(2026, 5, 15)


@pytest.fixture(autouse=True)
def _freeze_prompt_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(components_module, "date", _FixedDate)


def _etf_input(etf=None, *, deep: bool = False) -> ETFContextBuilderInput:
    return ETFContextBuilderInput(
        ticker="SXR8",
        question="What is the TER?",
        etf_data=etf,
        use_google_search=True,
        deep_analysis=deep,
        grounding=GROUNDING,
    )


def _comparison_input(*, short: bool) -> ComparisonContextBuilderInput:
    etfs = [make_etf("SXR8"), make_etf("VUSA", name="Vanguard S&P 500 UCITS ETF", fund_provider="Vanguard")]
    return ComparisonContextBuilderInput(
        tickers=["SXR8", "VUSA"],
        question="Compare SXR8 vs VUSA",
        etf_data_list=etfs,
        use_google_search=True,
        short_analysis=short,
        grounding=GROUNDING,
    )


def _empty_etf():
    return make_etf(holdings=[], sector_allocation=[], country_allocation=[])


_SOURCE_MARKER = "CRITICAL - Source Citation"
_SEARCH_HINTS = ("Google Search", "search online", "USE SEARCH WISELY")


GROUNDED_CASES: dict[str, Callable[[], str]] = {
    "none": lambda: NoneETFBuilder().build(_etf_input()),
    "basic_full": lambda: BasicETFBuilder().build(_etf_input(make_etf())),
    "basic_missing": lambda: BasicETFBuilder().build(_etf_input(None)),
    "detailed_short_full": lambda: DetailedETFBuilder().build(_etf_input(make_etf())),
    "detailed_short_empty": lambda: DetailedETFBuilder().build(_etf_input(_empty_etf())),
    "detailed_deep_full": lambda: DetailedETFBuilder().build(_etf_input(make_etf(), deep=True)),
    "detailed_deep_empty": lambda: DetailedETFBuilder().build(_etf_input(_empty_etf(), deep=True)),
    "comparison_short": lambda: ComparisonETFBuilder().build(_comparison_input(short=True)),
    "comparison_deep": lambda: ComparisonETFBuilder().build(_comparison_input(short=False)),
}


@pytest.mark.parametrize("name", sorted(GROUNDED_CASES))
def test_grounding_replaces_source_instructions_and_search_hints(name: str) -> None:
    prompt = GROUNDED_CASES[name]()
    assert prompt.count(GROUNDING) == 1
    assert _SOURCE_MARKER not in prompt
    for hint in _SEARCH_HINTS:
        assert hint not in prompt, hint


def test_grounded_detailed_still_reports_missing_data() -> None:
    prompt = DetailedETFBuilder().build(_etf_input(_empty_etf()))
    assert "Holdings data unavailable" in prompt
    assert "Sector allocation unavailable" in prompt
    assert "Country allocation unavailable" in prompt


def test_grounded_basic_still_flags_incomplete_data() -> None:
    assert "Some ETF data may be incomplete" in BasicETFBuilder().build(_etf_input(None))


def test_builder_inputs_require_grounding() -> None:
    with pytest.raises(TypeError):
        ETFContextBuilderInput(  # type: ignore[call-arg]
            ticker="SXR8", question="q", etf_data=None, use_google_search=False, deep_analysis=False
        )
    with pytest.raises(TypeError):
        ComparisonContextBuilderInput(  # type: ignore[call-arg]
            tickers=["SXR8", "VUSA"], question="q", etf_data_list=[], use_google_search=False
        )
