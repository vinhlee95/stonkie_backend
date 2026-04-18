from connectors.semantic_cache import TTLTier, detect_ttl_tier, normalize_question


class TestNormalizeQuestion:
    def test_lowercases_and_strips(self):
        assert normalize_question("  What Is AAPL Revenue?  ") == "what is aapl revenue?"

    def test_collapses_whitespace(self):
        assert normalize_question("revenue   trend   over   time") == "revenue trend over time"

    def test_preserves_meaningful_content(self):
        assert normalize_question("Q3 2024 earnings") == "q3 2024 earnings"


class TestDetectTTLTier:
    def test_historical_year(self):
        assert detect_ttl_tier("What was AAPL revenue in 2023?") == TTLTier.HISTORICAL

    def test_historical_quarter_year(self):
        assert detect_ttl_tier("Q3 2024 earnings report") == TTLTier.HISTORICAL

    def test_historical_fiscal_year(self):
        assert detect_ttl_tier("FY2023 balance sheet") == TTLTier.HISTORICAL

    def test_historical_past_years(self):
        assert detect_ttl_tier("Revenue trend over the last 3 years") == TTLTier.HISTORICAL

    def test_market_stock_price(self):
        assert detect_ttl_tier("What is the current stock price?") == TTLTier.MARKET

    def test_market_cap(self):
        assert detect_ttl_tier("What is Apple's market cap?") == TTLTier.MARKET

    def test_market_today(self):
        assert detect_ttl_tier("How is AAPL trading today?") == TTLTier.MARKET

    def test_recent_default(self):
        assert detect_ttl_tier("What is Apple's gross margin?") == TTLTier.RECENT

    def test_recent_latest(self):
        assert detect_ttl_tier("Latest quarterly revenue") == TTLTier.RECENT

    def test_market_takes_priority_over_historical(self):
        # "stock price in 2023" has both patterns — MARKET wins (checked first)
        assert detect_ttl_tier("stock price in 2023") == TTLTier.MARKET
