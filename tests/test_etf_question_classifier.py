"""Tests for ETF question classifier."""

import pytest

from services.etf_question_analyzer.classifier import ETFQuestionClassifier
from services.etf_question_analyzer.types import ETFDataRequirement, ETFQuestionType


@pytest.mark.asyncio
class TestETFQuestionClassifier:
    """Test suite for ETF question classification."""

    @pytest.fixture
    def classifier(self):
        """Create classifier instance."""
        return ETFQuestionClassifier()

    async def test_general_etf_question_no_ticker(self, classifier):
        """Test general ETF education question without ticker."""
        question = "What is the difference between physical and synthetic replication in ETFs?"
        ticker = "undefined"

        question_type, data_requirement = await classifier.classify_question(ticker, question)

        assert question_type == ETFQuestionType.GENERAL_ETF
        assert data_requirement == ETFDataRequirement.NONE

    async def test_basic_etf_overview_question(self, classifier):
        """Test basic ETF information question with ticker."""
        question = "What is the TER of this ETF and who is the fund provider?"
        ticker = "SXR8"

        question_type, data_requirement = await classifier.classify_question(ticker, question)

        assert question_type == ETFQuestionType.ETF_OVERVIEW
        assert data_requirement == ETFDataRequirement.BASIC

    async def test_detailed_holdings_question(self, classifier):
        """Test detailed analysis question requiring holdings data."""
        question = "Show me the top 10 holdings and their weights"
        ticker = "CSPX"

        question_type, data_requirement = await classifier.classify_question(ticker, question)

        assert question_type == ETFQuestionType.ETF_DETAILED_ANALYSIS
        assert data_requirement == ETFDataRequirement.DETAILED
