"""Test ticker extraction functionality."""

import asyncio
import logging

from services.etf_question_analyzer.ticker_extractor import ETFTickerExtractor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def test_extraction():
    """Test various ticker extraction scenarios."""
    extractor = ETFTickerExtractor()

    test_cases = [
        # Regex path (explicit tickers)
        ("Compare SXR8 vs SPYY", "Regex: explicit tickers"),
        ("What's the difference between SXR8 and SPYY?", "Regex: question format"),
        # AI path (ETF names)
        ("Compare iShares Core S&P 500 to SPDR MSCI World", "AI: ETF names"),
        ("iShares S&P 500 vs SPDR MSCI World", "AI: short names"),
        # Edge cases
        ("Tell me about SXR8", "Single ticker (no comparison)"),
        ("Compare INVALID vs FAKE", "Invalid tickers"),
        ("Compare SXR8 to fake ticker", "Mixed valid/invalid"),
    ]

    print("\n" + "=" * 80)
    print("TICKER EXTRACTION TESTS")
    print("=" * 80 + "\n")

    for question, description in test_cases:
        print(f"\n[TEST] {description}")
        print(f"Question: {question}")
        print("-" * 80)

        try:
            tickers = await extractor.extract_tickers(question)
            if tickers:
                print(f"✓ Extracted {len(tickers)} tickers: {tickers}")
            else:
                print("✗ No comparison detected (< 2 valid tickers)")
        except Exception as e:
            print(f"✗ Error: {e}")
            logger.exception("Extraction failed")

        print()

    print("=" * 80)
    print("Tests complete!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(test_extraction())
