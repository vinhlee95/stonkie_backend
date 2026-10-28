"""Answer prompts state the newest quarter in the data, never a calendar-derived "latest quarter"."""

from datetime import date

import pytest

import services.question_analyzer.context_builders.components as components_module
from services.question_analyzer.context_builders.base import ContextBuilderInput
from services.question_analyzer.context_builders.components import PromptComponents
from services.question_analyzer.context_builders.detailed_builder import DetailedContextBuilder
from services.question_analyzer.context_builders.quarterly_builder import QuarterlyContextBuilder


class _FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 25)


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr(components_module, "date", _FixedDate)


def _input(quarterly):
    return ContextBuilderInput(
        ticker="MSFT",
        question="How did Microsoft's latest quarter go?",
        company_fundamental=None,
        annual_statements=[],
        quarterly_statements=quarterly,
    )


def test_states_newest_quarter_from_data_regardless_of_order():
    quarterly = [{"period_end_quarter": "9/30/2025"}, {"period_end_quarter": "6/30/2026"}]

    context = PromptComponents.answer_date_context(quarterly)

    assert "Today is October 25, 2026." in context
    assert "Latest quarter in the financial statements below: the quarter ended June 30, 2026." in context


def test_no_calendar_quarter_assertion():
    context = PromptComponents.answer_date_context([{"period_end_quarter": "6/30/2026"}])

    assert "2026-Q3" not in context
    assert "most recently completed reporting quarter" not in context


def test_without_quarterly_data_only_states_today():
    context = PromptComponents.answer_date_context(None)

    assert context.startswith("Today is October 25, 2026.")
    assert "Latest quarter" not in context
    assert "2026-Q3" not in context


def test_ignores_unparseable_periods():
    context = PromptComponents.answer_date_context([{"period_end_quarter": "TTM"}, {"period_end_quarter": None}])

    assert "Latest quarter" not in context


@pytest.mark.parametrize("builder", [QuarterlyContextBuilder(), DetailedContextBuilder()])
def test_builders_pass_quarterly_statements_to_date_context(builder):
    prompt = builder.build(_input([{"period_end_quarter": "6/30/2026", "filing_10q_url": "https://sec.gov/q.htm"}]))

    assert "the quarter ended June 30, 2026" in prompt
    assert "2026-Q3" not in prompt


def test_classifier_date_context_unchanged():
    # current_date() still feeds the router/reformulator prompts; only answer prompts changed.
    assert "most recently completed reporting quarter is 2026-Q3" in PromptComponents.current_date()
