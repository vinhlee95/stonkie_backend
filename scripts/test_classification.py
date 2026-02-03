"""Test ETF question classification with comparison detection."""

import asyncio
import logging

from services.etf_question_analyzer.classifier import ETFQuestionClassifier
from services.etf_question_analyzer.types import ETFQuestionType

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_classification():
    """Test classification including comparison detection."""
    classifier = ETFQuestionClassifier()

    test_cases = [
        # Comparison questions
        ("Compare SXR8 vs SPYY", "SXR8", "Should detect comparison"),
        ("What's difference between SXR8 and SPYY?", "SXR8", "Should detect comparison"),
        ("SXR8 vs SPYY TER comparison", "SXR8", "Should detect comparison"),
        # Single ETF questions
        ("What is the TER of SXR8?", "SXR8", "Should be ETF_OVERVIEW"),
        ("Show me top holdings for SXR8", "SXR8", "Should be ETF_DETAILED_ANALYSIS"),
        ("What is an ETF?", "undefined", "Should be GENERAL_ETF"),
    ]

    print("\n" + "=" * 80)
    print("ETF CLASSIFICATION TESTS")
    print("=" * 80 + "\n")

    for question, ticker, description in test_cases:
        print(f"\n[TEST] {description}")
        print(f"Question: {question}")
        print(f"Ticker: {ticker}")
        print("-" * 80)

        try:
            question_type, data_requirement, comparison_tickers = await classifier.classify_question(ticker, question)

            print(f"✓ Type: {question_type.value}")
            print(f"  Data: {data_requirement.value}")

            if question_type == ETFQuestionType.ETF_COMPARISON:
                print(f"  Tickers: {comparison_tickers}")
            elif comparison_tickers:
                print("  WARNING: Got tickers but not comparison type")

        except Exception as e:
            print(f"✗ Error: {e}")
            logger.exception("Classification failed")

        print()

    print("=" * 80)
    print("Tests complete!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(test_classification())
