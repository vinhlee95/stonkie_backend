"""Tests for QuestionClassifier.classify_data_and_period_requirement (merged classifier)."""

import asyncio
from unittest.mock import MagicMock

from core.financial_statement_type import FinancialStatementType
from services.question_analyzer.classifier import QuestionClassifier
from services.question_analyzer.types import FinancialDataRequirement, FinancialPeriodRequirement


def _make_classifier_with_llm_response(response_text: str) -> tuple[QuestionClassifier, MagicMock]:
    """Build a QuestionClassifier whose underlying agent yields a fixed text response."""
    mock_agent = MagicMock()

    def fake_generate_content(prompt: str):
        # generate_content is a sync generator that yields text chunks
        yield response_text

    mock_agent.generate_content = MagicMock(side_effect=fake_generate_content)
    classifier = QuestionClassifier(agent=mock_agent)
    return classifier, mock_agent


def _make_classifier_with_llm_error(exc: Exception) -> tuple[QuestionClassifier, MagicMock]:
    """Build a QuestionClassifier whose underlying agent raises when iterated."""
    mock_agent = MagicMock()

    def raising_generate_content(prompt: str):
        raise exc
        yield  # pragma: no cover  -- make this a generator

    mock_agent.generate_content = MagicMock(side_effect=raising_generate_content)
    classifier = QuestionClassifier(agent=mock_agent)
    return classifier, mock_agent


class TestClassifyDataAndPeriodRequirementFastPaths:
    def test_quarterly_keyword_short_circuits_without_llm_call(self):
        classifier, mock_agent = _make_classifier_with_llm_response("ignored")

        data_req, period_req, rel_stmts = asyncio.run(
            classifier.classify_data_and_period_requirement(
                "AAPL", "Summarize Apple's latest quarterly earnings report"
            )
        )

        assert data_req == FinancialDataRequirement.QUARTERLY_SUMMARY
        assert period_req is None
        assert rel_stmts is None
        mock_agent.generate_content.assert_not_called()

    def test_annual_keyword_short_circuits_without_llm_call(self):
        classifier, mock_agent = _make_classifier_with_llm_response("ignored")

        data_req, period_req, rel_stmts = asyncio.run(
            classifier.classify_data_and_period_requirement(
                "AAPL", "What were the highlights from Apple's 10-K filing?"
            )
        )

        assert data_req == FinancialDataRequirement.ANNUAL_SUMMARY
        assert period_req is None
        assert rel_stmts is None
        mock_agent.generate_content.assert_not_called()


class TestClassifyDataAndPeriodRequirementLLMPath:
    def test_detailed_with_num_periods(self):
        response = (
            '{"data_requirement": "detailed", '
            '"period_requirement": {"period_type": "annual", '
            '"specific_years": null, "specific_quarters": null, "num_periods": 3}, '
            '"relevant_statements": ["income_statement"]}'
        )
        classifier, mock_agent = _make_classifier_with_llm_response(response)

        data_req, period_req, rel_stmts = asyncio.run(
            classifier.classify_data_and_period_requirement(
                "AAPL", "What is Apple revenue and profit margin trend over the last 3 years?"
            )
        )

        assert data_req == FinancialDataRequirement.DETAILED
        assert period_req == FinancialPeriodRequirement(
            period_type="annual", specific_years=None, specific_quarters=None, num_periods=3
        )
        assert rel_stmts == [FinancialStatementType.INCOME_STATEMENT]
        mock_agent.generate_content.assert_called_once()

    def test_detailed_with_specific_years(self):
        response = (
            '{"data_requirement": "detailed", '
            '"period_requirement": {"period_type": "annual", '
            '"specific_years": [2023, 2024], "specific_quarters": null, "num_periods": null}}'
        )
        classifier, _ = _make_classifier_with_llm_response(response)

        data_req, period_req, rel_stmts = asyncio.run(
            classifier.classify_data_and_period_requirement("AAPL", "What is Apple's revenue in 2023 and 2024?")
        )

        assert data_req == FinancialDataRequirement.DETAILED
        assert period_req is not None
        assert period_req.period_type == "annual"
        assert period_req.specific_years == [2023, 2024]
        assert period_req.specific_quarters is None
        assert period_req.num_periods is None
        assert rel_stmts == list(FinancialStatementType.all_ordered())

    def test_detailed_missing_relevant_statements_defaults_to_all_three(self):
        response = '{"data_requirement": "detailed", "period_requirement": null}'
        classifier, _ = _make_classifier_with_llm_response(response)

        data_req, period_req, rel_stmts = asyncio.run(
            classifier.classify_data_and_period_requirement("AAPL", "Broad financial overview")
        )

        assert data_req == FinancialDataRequirement.DETAILED
        assert rel_stmts == list(FinancialStatementType.all_ordered())

    def test_detailed_all_three_permuted_normalized_to_canonical_order(self):
        response = (
            '{"data_requirement": "detailed", '
            '"period_requirement": {"period_type": "annual", '
            '"specific_years": null, "specific_quarters": null, "num_periods": 3}, '
            '"relevant_statements": ["cash_flow", "income_statement", "balance_sheet"]}'
        )
        classifier, _ = _make_classifier_with_llm_response(response)

        data_req, _, rel_stmts = asyncio.run(
            classifier.classify_data_and_period_requirement("AAPL", "Full financial health check")
        )

        assert data_req == FinancialDataRequirement.DETAILED
        assert rel_stmts == list(FinancialStatementType.all_ordered())

    def test_basic_returns_none_period(self):
        response = '{"data_requirement": "basic", "period_requirement": null}'
        classifier, _ = _make_classifier_with_llm_response(response)

        data_req, period_req, rel_stmts = asyncio.run(
            classifier.classify_data_and_period_requirement("AAPL", "What is Apple's market cap?")
        )

        assert data_req == FinancialDataRequirement.BASIC
        assert period_req is None
        assert rel_stmts is None

    def test_none_returns_none_period(self):
        response = '{"data_requirement": "none", "period_requirement": null}'
        classifier, _ = _make_classifier_with_llm_response(response)

        data_req, period_req, rel_stmts = asyncio.run(
            classifier.classify_data_and_period_requirement("AAPL", "What does Apple do?")
        )

        assert data_req == FinancialDataRequirement.NONE
        assert period_req is None
        assert rel_stmts is None

    def test_markdown_json_wrapper_is_parsed(self):
        response = (
            "Sure, here is the classification:\n"
            "```json\n"
            '{"data_requirement": "detailed", '
            '"period_requirement": {"period_type": "annual", "specific_years": null, '
            '"specific_quarters": null, "num_periods": 5}}\n'
            "```"
        )
        classifier, _ = _make_classifier_with_llm_response(response)

        data_req, period_req, rel_stmts = asyncio.run(
            classifier.classify_data_and_period_requirement("AAPL", "Show me Apple's 5-year revenue growth")
        )

        assert data_req == FinancialDataRequirement.DETAILED
        assert period_req is not None
        assert period_req.num_periods == 5
        assert rel_stmts == list(FinancialStatementType.all_ordered())

    def test_detailed_with_missing_period_block_uses_fallback(self):
        # data_requirement says detailed but period_requirement is missing entirely
        response = '{"data_requirement": "detailed"}'
        classifier, _ = _make_classifier_with_llm_response(response)

        data_req, period_req, rel_stmts = asyncio.run(
            classifier.classify_data_and_period_requirement("AAPL", "What is Apple's debt?")
        )

        assert data_req == FinancialDataRequirement.DETAILED
        assert period_req == FinancialPeriodRequirement(period_type="annual", num_periods=3)
        assert rel_stmts == list(FinancialStatementType.all_ordered())

    def test_llm_exception_falls_back_to_basic_none(self):
        classifier, _ = _make_classifier_with_llm_error(RuntimeError("openrouter down"))

        data_req, period_req, rel_stmts = asyncio.run(
            classifier.classify_data_and_period_requirement("AAPL", "What is Apple's revenue trend?")
        )

        assert data_req == FinancialDataRequirement.BASIC
        assert period_req is None
        assert rel_stmts is None

    def test_unparseable_json_falls_back_to_basic_none(self):
        classifier, _ = _make_classifier_with_llm_response("totally not json at all")

        data_req, period_req, rel_stmts = asyncio.run(
            classifier.classify_data_and_period_requirement("AAPL", "What is Apple's revenue?")
        )

        assert data_req == FinancialDataRequirement.BASIC
        assert period_req is None
        assert rel_stmts is None

    def test_quarterly_summary_via_llm_with_period(self):
        response = (
            '{"data_requirement": "quarterly_summary", '
            '"period_requirement": {"period_type": "quarterly", '
            '"specific_years": null, "specific_quarters": ["2024-Q3"], "num_periods": null}}'
        )
        classifier, _ = _make_classifier_with_llm_response(response)

        data_req, period_req, rel_stmts = asyncio.run(
            classifier.classify_data_and_period_requirement("AAPL", "How did Apple do in Q3 2024?")
        )

        assert data_req == FinancialDataRequirement.QUARTERLY_SUMMARY
        assert period_req is not None
        assert period_req.period_type == "quarterly"
        assert period_req.specific_quarters == ["2024-Q3"]
        assert rel_stmts is None

    def test_quarterly_summary_missing_period_block_falls_back_quarterly(self):
        # Use a question that does NOT trigger keyword fast path
        response = '{"data_requirement": "quarterly_summary"}'
        classifier, _ = _make_classifier_with_llm_response(response)

        data_req, period_req, rel_stmts = asyncio.run(
            classifier.classify_data_and_period_requirement("AAPL", "How did Apple do last quarter financially?")
        )

        assert data_req == FinancialDataRequirement.QUARTERLY_SUMMARY
        assert period_req == FinancialPeriodRequirement(period_type="quarterly", num_periods=1)
        assert rel_stmts is None

    def test_annual_summary_missing_period_block_falls_back_annual(self):
        # Use a question that does NOT trigger keyword fast path
        response = '{"data_requirement": "annual_summary"}'
        classifier, _ = _make_classifier_with_llm_response(response)

        data_req, period_req, rel_stmts = asyncio.run(
            classifier.classify_data_and_period_requirement("AAPL", "How did Apple perform for the full year?")
        )

        assert data_req == FinancialDataRequirement.ANNUAL_SUMMARY
        assert period_req == FinancialPeriodRequirement(period_type="annual", num_periods=1)
        assert rel_stmts is None

    def test_unknown_data_requirement_falls_back_to_basic(self):
        response = '{"data_requirement": "unknown_value", "period_requirement": null}'
        classifier, _ = _make_classifier_with_llm_response(response)

        data_req, period_req, rel_stmts = asyncio.run(
            classifier.classify_data_and_period_requirement("AAPL", "Something unusual")
        )

        assert data_req == FinancialDataRequirement.BASIC
        assert period_req is None
        assert rel_stmts is None
