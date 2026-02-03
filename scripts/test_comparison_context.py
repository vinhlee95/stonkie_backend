"""Test comparison context builder."""

from connectors.etf_fundamental import ETFFundamentalConnector
from services.etf_question_analyzer.context_builders.comparison_builder import (
    ComparisonContextBuilderInput,
    ComparisonETFBuilder,
)


def test_comparison_context():
    """Test comparison context generation."""
    connector = ETFFundamentalConnector()

    # Fetch 2 ETFs
    sxr8 = connector.get_by_ticker("SXR8")
    spyy = connector.get_by_ticker("SPYY")

    if not sxr8 or not spyy:
        print("Error: ETFs not found in database")
        return

    print("\n" + "=" * 80)
    print("COMPARISON CONTEXT BUILDER TEST")
    print("=" * 80 + "\n")

    # Build comparison context
    builder = ComparisonETFBuilder()
    input_data = ComparisonContextBuilderInput(
        tickers=["SXR8", "SPYY"],
        question="Compare TER and holdings concentration",
        etf_data_list=[sxr8, spyy],
        use_google_search=False,
    )

    context = builder.build(input_data)

    print("Generated Context:\n")
    print(context)
    print("\n" + "=" * 80)

    # Check key elements
    print("\nContext Validation:")
    checks = [
        ("Contains ETF names", sxr8.name in context and spyy.name in context),
        ("Contains tickers", "SXR8" in context and "SPYY" in context),
        ("Contains TER", "TER" in context or "ter" in context.lower()),
        ("Contains holdings info", "Holdings" in context or "holdings" in context),
        ("Contains table instructions", "table" in context.lower()),
        ("Contains user question", "Compare TER and holdings concentration" in context),
    ]

    for check_name, passed in checks:
        status = "✓" if passed else "✗"
        print(f"{status} {check_name}")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    test_comparison_context()
