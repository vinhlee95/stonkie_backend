"""
Test script for ETFFundamentalConnector CRUD operations.
"""

from connectors.etf_fundamental import ETFFundamentalConnector

# Test data structure matching test_etf_scraper.py output
test_data = {
    "isin": "IE00B5BMR087",
    "ticker": "SXR8",
    "name": "iShares Core S&P 500 UCITS ETF (Acc)",
    "fund_provider": "iShares",
    "fund_size_millions": 55570,
    "ter_percent": 0.07,
    "replication_method": "Physical (Full replication)",
    "distribution_policy": "Accumulating",
    "fund_currency": "USD",
    "domicile": "IE",
    "launch_date": "2010-05-17",
    "index_tracked": "S&P 500",
    "holdings": [
        {"name": "Apple", "weight_percent": 7.04},
        {"name": "Microsoft", "weight_percent": 6.22},
        {"name": "NVIDIA", "weight_percent": 6.18},
    ],
    "sector_allocation": [
        {"sector": "Information Technology", "weight_percent": 29.54},
        {"sector": "Financials", "weight_percent": 13.06},
        {"sector": "Health Care", "weight_percent": 11.52},
    ],
    "country_allocation": [
        {"country": "United States", "weight_percent": 97.52},
        {"country": "Ireland", "weight_percent": 2.32},
        {"country": "Other", "weight_percent": 0.16},
    ],
    "source_url": "https://www.justetf.com/en/etf-profile.html?isin=IE00B5BMR087",
}

connector = ETFFundamentalConnector()

# Test upsert (create)
print("Testing upsert (create)...")
result = connector.upsert(test_data)
print(f"✅ Created: {result.isin} - {result.name}")
print(f"   Holdings count: {len(result.holdings)}")
print(f"   Sectors count: {len(result.sector_allocation)}")
print(f"   Countries count: {len(result.country_allocation)}")

# Test get_by_isin
print("\n✅ Testing get_by_isin...")
etf = connector.get_by_isin("IE00B5BMR087")
assert etf is not None, "ETF not found by ISIN"
print(f"   Retrieved: {etf.name}")
print(f"   TER: {etf.ter_percent}%")

# Test get_by_ticker
print("\n✅ Testing get_by_ticker...")
etf = connector.get_by_ticker("SXR8")
assert etf is not None, "ETF not found by ticker"
print(f"   Retrieved: {etf.name}")
print(f"   Fund Size: ${etf.fund_size_millions}M")

# Test get_by_provider
print("\n✅ Testing get_by_provider...")
etfs = connector.get_by_provider("iShares")
print(f"   Found {len(etfs)} iShares ETFs")
assert len(etfs) >= 1, "Expected at least 1 iShares ETF"

# Test upsert (update)
print("\n✅ Testing upsert (update)...")
test_data["fund_size_millions"] = 60000  # Change value
result = connector.upsert(test_data)
assert result.fund_size_millions == 60000, "Fund size not updated"
print(f"   Updated fund size to ${result.fund_size_millions}M")

# Test get_all
print("\n✅ Testing get_all...")
all_etfs = connector.get_all()
print(f"   Total ETFs in database: {len(all_etfs)}")
assert len(all_etfs) >= 1, "Expected at least 1 ETF"

# Test holdings DTO conversion
print("\n✅ Testing DTO conversions...")
etf = connector.get_by_isin("IE00B5BMR087")
assert len(etf.holdings) == 3, "Expected 3 holdings"
assert etf.holdings[0].name == "Apple", "Expected Apple as first holding"
assert etf.holdings[0].weight_percent == 7.04, "Expected correct weight"
print(f"   Top holding: {etf.holdings[0].name} ({etf.holdings[0].weight_percent}%)")

# Test sector allocation DTO conversion
assert len(etf.sector_allocation) == 3, "Expected 3 sectors"
assert etf.sector_allocation[0].sector == "Information Technology"
print(f"   Top sector: {etf.sector_allocation[0].sector} ({etf.sector_allocation[0].weight_percent}%)")

# Test country allocation DTO conversion
assert len(etf.country_allocation) == 3, "Expected 3 countries"
assert etf.country_allocation[0].country == "United States"
print(f"   Top country: {etf.country_allocation[0].country} ({etf.country_allocation[0].weight_percent}%)")

# Test delete
print("\n✅ Testing delete_by_isin...")
deleted = connector.delete_by_isin("IE00B5BMR087")
assert deleted is True, "ETF should be deleted"
print("   ETF deleted successfully")

# Verify deletion
etf = connector.get_by_isin("IE00B5BMR087")
assert etf is None, "ETF should not exist after deletion"
print("   Verified deletion")

print("\n" + "=" * 50)
print("✅ ALL TESTS PASSED!")
print("=" * 50)
