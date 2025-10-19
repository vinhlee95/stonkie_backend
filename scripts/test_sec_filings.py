#!/usr/bin/env python3
"""
Test script for the simplified SEC filings fetcher
"""

from scripts.fetch_sec_filings import get_sec_filings


def test_sec_filings():
    """Test the get_sec_filings function"""

    print("=== Testing SEC Filings Fetcher ===\n")

    # Test 1: Get annual filings (10-K)
    print("1. Getting annual filings for AAPL...")
    annual_filings = get_sec_filings("AAPL", "annually", 2)

    if annual_filings:
        print(f"   Found {len(annual_filings)} annual filings")
        for i, filing in enumerate(annual_filings, 1):
            print(f"   {i}. Time: {filing['time']}")
            print(f"      Period: {filing['period']}")
            print(f"      Type: {filing['type']}")
            print(f"      URL: {filing['URL']}...")
            print()
    else:
        print("   No annual filings found")

    # Test 2: Get quarterly filings (10-Q)
    print("\n2. Getting quarterly filings for AAPL...")
    quarterly_filings = get_sec_filings("AAPL", "quarterly", 2)

    if quarterly_filings:
        print(f"   Found {len(quarterly_filings)} quarterly filings")
        for i, filing in enumerate(quarterly_filings, 1):
            print(f"   {i}. Time: {filing['time']}")
            print(f"      Period: {filing['period']}")
            print(f"      Type: {filing['type']}")
            print(f"      URL: {filing['URL']}...")
            print()
    else:
        print("   No quarterly filings found")


if __name__ == "__main__":
    test_sec_filings()
