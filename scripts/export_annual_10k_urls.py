#!/usr/bin/env python3
"""
Script to fetch and persist 10K filing URLs to the database

This script:
1. Fetches all records from company_financial_statement table
2. Uses the SEC filings fetcher to get 10K URLs for each company/year
3. Updates the filing_10k_url column with the fetched URLs
"""

import os
import sys
from collections import Counter, defaultdict
from typing import Dict, List, Tuple

from dotenv import load_dotenv

# Add the parent directory to the path so we can import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.orm import sessionmaker

from connectors.database import engine
from models.company_financial_statement import CompanyFinancialStatement
from scripts.fetch_sec_filings import get_sec_filings

# Load environment variables
load_dotenv()


def get_all_financial_records() -> List[CompanyFinancialStatement]:
    """
    Step 1: Fetch all records from company_financial_statement table

    Returns:
        List of CompanyFinancialStatement records
    """
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # Get all records from the table
        records = session.query(CompanyFinancialStatement).all()
        print(f"Found {len(records)} financial statement records in database")

        # Group by company for better logging
        companies = {}
        for record in records:
            if record.company_symbol not in companies:
                companies[record.company_symbol] = []
            companies[record.company_symbol].append(record.period_end_year)

        print("Companies and years found:")
        for symbol, years in companies.items():
            print(f"  {symbol}: {sorted(years)}")

        return records

    except Exception as e:
        print(f"Error fetching financial records: {e}")
        return []
    finally:
        session.close()


def fetch_10k_urls_for_company(ticker: str, years: List[int]) -> Dict[int, str]:
    """
    Step 2: Fetch 10K filing URLs for a specific company

    Args:
        ticker: Stock ticker symbol
        years: List of years we need 10K filings for

    Returns:
        Dictionary mapping year to 10K URL
    """
    print(f"\nFetching 10K filings for {ticker}...")

    # Fetch annual filings (10K) - get more than we need to cover all years
    max_years = len(years) + 10  # Get a few extra in case some years are missing
    annual_filings = get_sec_filings(ticker, "annually", max_years)

    if not annual_filings:
        print(f"  No annual filings found for {ticker}")
        return {}

    print(f"  Found {len(annual_filings)} annual filings for {ticker}")

    # Map filing dates to years and URLs
    year_to_url = {}

    for filing in annual_filings:
        if filing.get("URL"):
            # Extract year from the filing time (ISO format: YYYY-MM-DD...)
            filing_time = filing.get("time", "")
            if filing_time:
                filing_year = int(filing_time[:4])  # Extract YYYY

                # Check if this year is one we need
                if filing_year in years:
                    year_to_url[filing_year] = filing["URL"]
                    print(f"  Mapped {filing_year}: {filing['URL'][:80]}...")

    print(f"  Successfully mapped {len(year_to_url)} years to URLs")
    return year_to_url


def update_10k_urls_in_database(updates: List[Dict]) -> int:
    """
    Step 3: Update the filing_10k_url column in the database

    Args:
        updates: List of dictionaries with 'id', 'ticker', 'year', and 'url'

    Returns:
        Number of records updated
    """
    if not updates:
        print("No updates to perform")
        return 0

    Session = sessionmaker(bind=engine)
    session = Session()
    updated_count = 0

    try:
        for update in updates:
            record = session.query(CompanyFinancialStatement).filter_by(id=update["id"]).first()
            if record:
                record.filing_10k_url = update["url"]
                print(f"  Updated {update['ticker']} {update['year']}: {update['url'][:60]}...")
                updated_count += 1

        session.commit()
        print(f"Successfully updated {updated_count} records")

    except Exception as e:
        session.rollback()
        print(f"Error updating database: {e}")
        updated_count = 0
    finally:
        session.close()

    return updated_count


def print_summary_table(summary_data: Dict) -> None:
    """
    Print a comprehensive summary table of the backfill results

    Args:
        summary_data: Dictionary containing all the summary statistics
    """
    print("\n" + "=" * 80)
    print("                         BACKFILL SUMMARY REPORT")
    print("=" * 80)

    # Overall Statistics
    total_records = summary_data["total_records"]
    already_filled = summary_data["already_filled"]
    newly_filled = summary_data["newly_filled"]
    failed_to_fill = summary_data["failed_to_fill"]

    print("\n📊 OVERALL STATISTICS:")
    print(f"   Total Records in Database:     {total_records:>6}")
    print(f"   Already Had URLs:              {already_filled:>6}")
    print(f"   Successfully Backfilled:       {newly_filled:>6}")
    print(f"   Failed to Backfill:            {failed_to_fill:>6}")
    print(f"   Current Coverage:              {((already_filled + newly_filled) / total_records * 100):>5.1f}%")

    # Company-wise breakdown
    print("\n📈 COMPANY-WISE BREAKDOWN:")
    print("   " + "-" * 72)
    print("   Company    Total  Already  New   Failed  Coverage  Reason for Failures")
    print("   " + "-" * 72)

    for company, data in summary_data["company_details"].items():
        total = data["total"]
        already = data["already_filled"]
        new = data["newly_filled"]
        failed = data["failed"]
        coverage = ((already + new) / total * 100) if total > 0 else 0
        reason = data["failure_reason"]

        print(f"   {company:<8} {total:>5} {already:>7} {new:>5} {failed:>7} {coverage:>7.1f}%  {reason}")

    # Failure reasons summary
    print("\n❌ FAILURE REASONS SUMMARY:")
    failure_counts = summary_data["failure_reasons"]
    if failure_counts:
        for reason, count in failure_counts.items():
            print(f"   • {reason}: {count} records")
    else:
        print("   • No failures!")

    # Year coverage analysis
    print("\n📅 YEAR COVERAGE ANALYSIS:")
    year_stats = summary_data["year_coverage"]
    if year_stats:
        print("   Year    Total  Filled  Missing  Coverage")
        print("   " + "-" * 38)
        for year in sorted(year_stats.keys()):
            stats = year_stats[year]
            total = stats["total"]
            filled = stats["filled"]
            missing = stats["missing"]
            coverage = (filled / total * 100) if total > 0 else 0
            print(f"   {year}    {total:>5}  {filled:>6}  {missing:>7}  {coverage:>7.1f}%")

    print("\n" + "=" * 80)


def analyze_records_comprehensive(records: List[CompanyFinancialStatement]) -> Tuple[Dict, Dict]:
    """
    Analyze all records and categorize them for comprehensive reporting

    Returns:
        Tuple of (companies_data, initial_stats)
    """
    companies_data = {}
    initial_stats = {
        "total_records": len(records),
        "already_filled": 0,
        "need_backfill": 0,
        "company_details": defaultdict(lambda: {"total": 0, "already_filled": 0, "need_backfill": 0, "years": []}),
        "year_coverage": defaultdict(lambda: {"total": 0, "filled": 0, "missing": 0}),
    }

    for record in records:
        ticker = record.company_symbol
        year = record.period_end_year
        has_url = bool(record.filing_10k_url and record.filing_10k_url.strip())

        # Initialize company data
        if ticker not in companies_data:
            companies_data[ticker] = []

        companies_data[ticker].append(
            {"id": record.id, "year": year, "current_url": record.filing_10k_url, "has_url": has_url}
        )

        # Update statistics
        initial_stats["company_details"][ticker]["total"] += 1
        initial_stats["company_details"][ticker]["years"].append(year)
        initial_stats["year_coverage"][year]["total"] += 1

        if has_url:
            initial_stats["already_filled"] += 1
            initial_stats["company_details"][ticker]["already_filled"] += 1
            initial_stats["year_coverage"][year]["filled"] += 1
        else:
            initial_stats["need_backfill"] += 1
            initial_stats["company_details"][ticker]["need_backfill"] += 1
            initial_stats["year_coverage"][year]["missing"] += 1

    return companies_data, initial_stats
    """Main execution function"""
    print("=== 10K Filing URL Updater ===\n")

    # Step 1: Get all financial records from database
    print("Step 1: Fetching all financial statement records...")
    records = get_all_financial_records()

    if not records:
        print("No records found. Exiting.")
        return

    # Group records by company to process efficiently
    companies_data = {}
    for record in records:
        ticker = record.company_symbol
        if ticker not in companies_data:
            companies_data[ticker] = []
        companies_data[ticker].append(
            {"id": record.id, "year": record.period_end_year, "current_url": record.filing_10k_url}
        )

    print(f"\nStep 2: Processing {len(companies_data)} companies...")

    all_updates = []

    # Step 2 & 3: For each company, fetch 10K URLs and prepare updates
    for ticker, company_records in companies_data.items():
        print(f"\nProcessing {ticker}...")

        # Skip non-US tickers (SEC only covers US companies)
        if "." in ticker or "-" in ticker:
            print(f"  Skipping {ticker} - appears to be non-US ticker")
            continue

        # Check if any records need updating (don't have URLs yet)
        records_needing_update = [r for r in company_records if not r["current_url"]]
        if not records_needing_update:
            print(f"  All records for {ticker} already have URLs")
            continue

        years_needed = [r["year"] for r in records_needing_update]
        print(f"  Need URLs for years: {sorted(years_needed)}")

        # Fetch 10K URLs for this company
        year_to_url = fetch_10k_urls_for_company(ticker, years_needed)

        # Prepare updates for records that we found URLs for
        for record in records_needing_update:
            if record["year"] in year_to_url:
                all_updates.append(
                    {"id": record["id"], "ticker": ticker, "year": record["year"], "url": year_to_url[record["year"]]}
                )

    # Step 3: Update database with all the URLs we found
    print(f"\nStep 3: Updating database with {len(all_updates)} URLs...")
    if all_updates:
        updated_count = update_10k_urls_in_database(all_updates)
        print(f"\nCompleted! Updated {updated_count} out of {len(all_updates)} records.")
    else:
        print("No updates needed - all records already have URLs or no URLs were found.")


def main():
    """Enhanced main execution function with comprehensive logging"""
    print("=== 10K Filing URL Updater ===\n")

    # Step 1: Get all financial records from database
    print("Step 1: Fetching all financial statement records...")
    records = get_all_financial_records()

    if not records:
        print("No records found. Exiting.")
        return

    # Analyze records comprehensively
    companies_data, initial_stats = analyze_records_comprehensive(records)

    print("\nInitial Analysis:")
    print(f"  Total records: {initial_stats['total_records']}")
    print(f"  Already have URLs: {initial_stats['already_filled']}")
    print(f"  Need backfill: {initial_stats['need_backfill']}")

    # Initialize tracking for final summary
    summary_data = {
        "total_records": initial_stats["total_records"],
        "already_filled": initial_stats["already_filled"],
        "newly_filled": 0,
        "failed_to_fill": 0,
        "company_details": {},
        "failure_reasons": Counter(),
        "year_coverage": initial_stats["year_coverage"].copy(),
    }

    print(f"\nStep 2: Processing {len(companies_data)} companies...")

    all_updates = []

    # Process each company
    for ticker, company_records in companies_data.items():
        print(f"\nProcessing {ticker}...")

        # Initialize company summary
        company_summary = {
            "total": len(company_records),
            "already_filled": len([r for r in company_records if r["has_url"]]),
            "newly_filled": 0,
            "failed": 0,
            "failure_reason": "",
        }

        # Skip non-US tickers (SEC only covers US companies)
        if "." in ticker or "-" in ticker:
            reason = "Non-US ticker (not in SEC database)"
            print(f"  Skipping {ticker} - {reason}")
            records_needing_update = [r for r in company_records if not r["has_url"]]
            company_summary["failed"] = len(records_needing_update)
            company_summary["failure_reason"] = reason
            summary_data["failure_reasons"][reason] += len(records_needing_update)
            summary_data["company_details"][ticker] = company_summary
            continue

        # Check if any records need updating (don't have URLs yet)
        records_needing_update = [r for r in company_records if not r["has_url"]]
        if not records_needing_update:
            print(f"  All records for {ticker} already have URLs")
            summary_data["company_details"][ticker] = company_summary
            continue

        years_needed = [r["year"] for r in records_needing_update]
        print(f"  Need URLs for years: {sorted(years_needed)}")

        # Fetch 10K URLs for this company
        year_to_url = fetch_10k_urls_for_company(ticker, years_needed)

        if not year_to_url:
            reason = "No SEC filings found (CIK not in known mappings)"
            company_summary["failed"] = len(records_needing_update)
            company_summary["failure_reason"] = reason
            summary_data["failure_reasons"][reason] += len(records_needing_update)
        else:
            # Process each record that needed updating
            for record in records_needing_update:
                if record["year"] in year_to_url:
                    # Success - found URL for this year
                    all_updates.append(
                        {
                            "id": record["id"],
                            "ticker": ticker,
                            "year": record["year"],
                            "url": year_to_url[record["year"]],
                        }
                    )
                    company_summary["newly_filled"] += 1
                else:
                    # Failed - no filing found for this year
                    reason = f"No 10-K filing found for year {record['year']}"
                    company_summary["failed"] += 1
                    if not company_summary["failure_reason"]:
                        company_summary["failure_reason"] = "Missing filings for some years"
                    summary_data["failure_reasons"]["No filing found for specific year"] += 1

        summary_data["company_details"][ticker] = company_summary

    # Step 3: Update database with all the URLs we found
    print(f"\nStep 3: Updating database with {len(all_updates)} URLs...")
    if all_updates:
        updated_count = update_10k_urls_in_database(all_updates)
        summary_data["newly_filled"] = updated_count

        # Update year coverage statistics
        for update in all_updates[:updated_count]:  # Only count successfully updated ones
            year = update["year"]
            summary_data["year_coverage"][year]["filled"] += 1
            summary_data["year_coverage"][year]["missing"] -= 1

    # Calculate final statistics
    summary_data["failed_to_fill"] = (
        summary_data["total_records"] - summary_data["already_filled"] - summary_data["newly_filled"]
    )

    # Print comprehensive summary
    print_summary_table(summary_data)


if __name__ == "__main__":
    main()
