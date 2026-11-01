"""Narrative answer format + search instruction prompt components and where the stock builders use them."""

from __future__ import annotations

from services.question_analyzer.context_builders import ContextBuilderInput
from services.question_analyzer.context_builders.basic_builder import BasicContextBuilder
from services.question_analyzer.context_builders.comparison_builder import (
    ComparisonCompanyBuilder,
    ComparisonCompanyBuilderInput,
)
from services.question_analyzer.context_builders.components import PromptComponents
from services.question_analyzer.context_builders.detailed_builder import DetailedContextBuilder
from services.question_analyzer.context_builders.none_builder import NoneContextBuilder

NARRATIVE = PromptComponents.narrative_answer_format()


def _input(**overrides) -> ContextBuilderInput:
    values = dict(
        ticker="goog",
        question="Whats the comp profit margin trending?",
        company_fundamental={"Name": "Alphabet"},
        annual_statements=[],
        quarterly_statements=[{"period_end_quarter": "6/30/2026"}],
        deep_analysis=False,
        use_google_search=False,
    )
    values.update(overrides)
    return ContextBuilderInput(**values)


def test_narrative_format_leads_with_takeaway_then_drivers_then_implication():
    text = NARRATIVE.lower()

    assert "takeaway" in text
    assert "drivers" in text
    assert "what to watch" in text
    assert text.index("takeaway") < text.index("drivers") < text.index("what to watch")


def test_narrative_format_bans_number_dumps_and_metric_preambles():
    assert "every period" in NARRATIVE
    assert "not a reported metric" in NARRATIVE
    assert "chart" in NARRATIVE


def test_narrative_format_allows_events_only_from_sources():
    assert "only when they appear in the Sources" in NARRATIVE
    assert "web_search results" in NARRATIVE
    assert "training knowledge" in NARRATIVE


def test_narrative_format_says_unexplained_moves_are_unexplained():
    assert "not explained by the sources" in NARRATIVE
    assert "likely" in NARRATIVE


def test_narrative_format_has_no_fixed_sentence_cap():
    assert "well short of a page" in NARRATIVE
    assert "under 60 words" not in NARRATIVE
    assert "1-2 sentences" not in NARRATIVE


def test_search_instruction_names_the_tool_and_says_search_before_writing():
    text = PromptComponents.search_instruction()

    assert "web_search" in text
    assert "before writing" in text.lower()
    assert "do not announce" in text.lower()


def test_grounding_rules_unchanged_for_shared_etf_use():
    assert "Do not introduce facts (numbers, dates, names, events)" in PromptComponents.grounding_rules()


def test_detailed_short_uses_narrative_format():
    prompt = DetailedContextBuilder().build(_input())

    assert NARRATIVE in prompt
    assert PromptComponents.section_structure_template() not in prompt


def test_basic_uses_narrative_format():
    prompt = BasicContextBuilder().build(_input())

    assert NARRATIVE in prompt
    assert "under 150 words" not in prompt


def test_none_uses_narrative_format():
    prompt = NoneContextBuilder().build(_input())

    assert NARRATIVE in prompt
    assert "under 150 words" not in prompt


def test_comparison_short_keeps_table_and_uses_narrative_format():
    prompt = ComparisonCompanyBuilder().build(
        ComparisonCompanyBuilderInput(
            tickers=["NVDA", "AMD"],
            question="How do Nvidia's margins compare with AMD?",
            companies_data=[],
            use_google_search=True,
            short_analysis=True,
        )
    )

    assert NARRATIVE in prompt
    assert "comparison table" in prompt
    assert "under 60 words" not in prompt
