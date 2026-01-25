#!/usr/bin/env python3
"""
Bare-minimum ETF scraper validation script - Phase 0.
Tests justetf.com scraping + Gemini AI parsing before building full infrastructure.

Usage:
    python scripts/test_etf_scraper.py <justetf_url>
    python scripts/test_etf_scraper.py --debug <justetf_url>

Example:
    python scripts/test_etf_scraper.py https://www.justetf.com/en/etf-profile.html?isin=IE00B5BMR087
"""

import argparse
import json
import logging
import re
import sys
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

from agent.agent import Agent

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def preprocess_html(html: str) -> str:
    """
    Remove unnecessary HTML content to reduce token usage and improve AI focus.

    Args:
        html: Raw HTML content

    Returns:
        Cleaned HTML with scripts, styles, and navigation removed
    """
    logger.info("Preprocessing HTML to remove noise...")

    # Remove script tags and their content
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)

    # Remove style tags and their content
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)

    # Remove navigation, header, footer elements (common non-content areas)
    html = re.sub(r"<nav[^>]*>.*?</nav>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<header[^>]*>.*?</header>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<footer[^>]*>.*?</footer>", "", html, flags=re.DOTALL | re.IGNORECASE)

    # Remove comments
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)

    # Compress whitespace
    html = re.sub(r"\s+", " ", html)

    logger.info(f"HTML preprocessed: {len(html)} characters after cleanup")
    return html


def extract_isin_from_url(url: str) -> str | None:
    """
    Extract ISIN code from justetf.com URL query parameters.

    Args:
        url: Full justetf.com URL

    Returns:
        ISIN code or None if not found
    """
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        isin = params.get("isin", [None])[0]
        if isin:
            logger.info(f"Extracted ISIN: {isin}")
        return isin
    except Exception as e:
        logger.error(f"Failed to extract ISIN from URL: {e}")
        return None


def fetch_etf_page(url: str, debug: bool = False) -> str | None:
    """
    Fetch ETF page HTML using Playwright.

    Args:
        url: justetf.com ETF profile URL
        debug: If True, save HTML to file

    Returns:
        Full page HTML or None if failed
    """
    try:
        with sync_playwright() as p:
            logger.info("Launching browser...")
            browser = p.chromium.launch(headless=True)

            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            page = context.new_page()

            logger.info(f"Navigating to {url}...")
            page.goto(url, timeout=20000)

            # Wait for page to load
            logger.info("Waiting for page load...")
            page.wait_for_load_state("networkidle", timeout=20000)
            page.wait_for_timeout(2000)

            # Handle cookie consent with multiple selector strategies
            cookie_selectors = [
                "button[data-consent-accept]",
                "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
                "button.consent-accept",
                "button:has-text('Accept all')",
            ]

            cookie_handled = False
            for selector in cookie_selectors:
                try:
                    logger.info(f"Trying cookie consent selector: {selector}")
                    page.wait_for_selector(selector, timeout=3000)
                    page.click(selector)
                    logger.info(f"✓ Cookie consent handled via {selector}")
                    page.wait_for_timeout(2000)
                    cookie_handled = True
                    break
                except Exception:
                    continue

            if not cookie_handled:
                logger.info("No cookie consent modal found or already accepted")

            # Wait for dynamic content to load
            logger.info("Waiting for dynamic content...")
            page.wait_for_timeout(2000)

            # Extract full page HTML
            html = page.content()
            logger.info(f"Extracted HTML ({len(html)} characters)")

            if debug:
                debug_file = "etf_page_debug.html"
                with open(debug_file, "w", encoding="utf-8") as f:
                    f.write(html)
                logger.info(f"Saved HTML to {debug_file}")

            browser.close()
            return html

    except Exception as e:
        logger.error(f"Failed to fetch page: {e}")
        return None


def extract_etf_data_with_ai(html: str, isin: str) -> dict[str, Any] | None:
    """
    Extract ETF data from HTML using Gemini AI.

    Args:
        html: Full page HTML
        isin: ETF ISIN code

    Returns:
        Extracted ETF data as dict or None if failed
    """
    try:
        logger.info("Initializing Gemini 2.5 Flash for extraction...")
        agent = Agent(model_type="gemini", model_name="gemini-2.5-flash")

        # Preprocess HTML to remove noise and reduce token usage
        html_cleaned = preprocess_html(html)

        # Truncate HTML to fit AI context window
        # Gemini 2.5 Flash supports 1M tokens (~4 chars/token), use 800k chars for safety
        # Preprocessing reduces size by ~30%, so 800k cleaned chars ≈ 1M original chars
        html_truncated = html_cleaned[:800000]
        logger.info(
            f"Using {len(html_truncated)} characters of HTML for AI extraction (full HTML: {len(html)} chars, cleaned: {len(html_cleaned)} chars)"
        )

        prompt = f"""You are an expert financial data extractor. Extract ETF data from the HTML page below and return ONLY valid JSON.

CRITICAL: The holdings, sector_allocation, and country_allocation arrays MUST be populated if the data exists in the HTML. Look carefully in tables and lists.

STEP-BY-STEP EXTRACTION PROCESS:

STEP 1: Basic Information
- name: Look in <h1> tag or class="etf-name" or page title
- isin: Use provided value: {isin}
- ticker: Look for "Ticker", "Symbol" fields
- fund_provider: Look for "Provider", "Fund provider", "Issuer" (e.g., iShares, Vanguard)

STEP 2: Financial Metrics
- fund_size_millions: Find "Fund size", "AUM", "Assets under management"
  * Convert: "€55.5bn" -> 55500, "$1.2m" -> 1.2
- ter_percent: Find "TER", "Total expense ratio", "Ongoing charges"
  * Convert: "0.07%" -> 0.07 (numeric, NOT string)

STEP 3: Fund Details
- replication_method: "Physical (Full replication)", "Synthetic", "Physical (Optimized sampling)"
- distribution_policy: "Accumulating", "Distributing", "Capitalisation"
- fund_currency: "USD", "EUR", "GBP" (NOT symbol)
- domicile: Country code like "IE", "LU", "US"
- launch_date: Format as YYYY-MM-DD
- index_tracked: Full index name like "S&P 500"

STEP 4: Holdings Array (CRITICAL - MUST EXTRACT)
Look for HTML sections with classes/ids like:
- class="holdings", class="top-holdings", id="holdings-table"
- <table> elements with headers "Name", "Weight", "Company"
- Section headings: "Top 10 Holdings", "Portfolio Holdings", "Largest Holdings"

Extract EVERY holding shown (typically 10-15 rows):
- name: Company/security name (e.g., "Apple Inc", "Microsoft Corp")
- weight_percent: Numeric value (e.g., "7.04%" -> 7.04)

Example format:
[
  {{"name": "Apple Inc", "weight_percent": 7.04}},
  {{"name": "Microsoft Corp", "weight_percent": 6.52}},
  {{"name": "NVIDIA Corp", "weight_percent": 5.11}}
]

STEP 5: Sector Allocation Array (CRITICAL - MUST EXTRACT)
Look for HTML sections with:
- class="sector-allocation", class="sector-breakdown"
- <table> with headers "Sector", "Weight", "Allocation"
- Section headings: "Sector Allocation", "Sector Breakdown", "Industry Breakdown"

Extract ALL sectors shown (typically 10-15 rows):
- sector: Sector name (e.g., "Information Technology", "Financials", "Health Care")
- weight_percent: Numeric value (e.g., "28.5%" -> 28.5)

Example format:
[
  {{"sector": "Information Technology", "weight_percent": 28.5}},
  {{"sector": "Financials", "weight_percent": 13.2}},
  {{"sector": "Health Care", "weight_percent": 12.8}}
]

STEP 6: Country Allocation Array (CRITICAL - MUST EXTRACT)
Look for HTML sections with:
- class="country-allocation", class="geographic-breakdown"
- <table> with headers "Country", "Weight", "Region"
- Section headings: "Country Allocation", "Geographic Breakdown"

Extract ALL countries shown (typically 5-15 rows):
- country: Country name (e.g., "United States", "Japan", "United Kingdom")
- weight_percent: Numeric value (e.g., "70.2%" -> 70.2)

Example format:
[
  {{"country": "United States", "weight_percent": 70.2}},
  {{"country": "Japan", "weight_percent": 5.8}},
  {{"country": "United Kingdom", "weight_percent": 4.1}}
]

FEW-SHOT EXAMPLES:

Example 1 - Good extraction with all arrays populated:
{{
  "name": "iShares Core S&P 500 UCITS ETF USD (Acc)",
  "isin": "IE00B5BMR087",
  "ticker": "CSPX",
  "fund_size_millions": 55500.0,
  "ter_percent": 0.07,
  "replication_method": "Physical (Full replication)",
  "distribution_policy": "Accumulating",
  "fund_currency": "USD",
  "domicile": "IE",
  "launch_date": "2010-05-19",
  "index_tracked": "S&P 500",
  "fund_provider": "iShares",
  "holdings": [
    {{"name": "Apple Inc", "weight_percent": 7.04}},
    {{"name": "Microsoft Corp", "weight_percent": 6.52}},
    {{"name": "NVIDIA Corp", "weight_percent": 5.11}},
    {{"name": "Amazon.com Inc", "weight_percent": 3.71}},
    {{"name": "Meta Platforms Inc", "weight_percent": 2.48}}
  ],
  "sector_allocation": [
    {{"sector": "Information Technology", "weight_percent": 28.5}},
    {{"sector": "Financials", "weight_percent": 13.2}},
    {{"sector": "Health Care", "weight_percent": 12.8}},
    {{"sector": "Consumer Discretionary", "weight_percent": 10.5}}
  ],
  "country_allocation": [
    {{"country": "United States", "weight_percent": 100.0}}
  ]
}}

Example 2 - Bad extraction with empty arrays (AVOID THIS):
{{
  "name": "Some ETF",
  "isin": "IE00B5BMR087",
  "holdings": [],
  "sector_allocation": [],
  "country_allocation": []
}}

OUTPUT REQUIREMENTS:
1. Return ONLY the JSON object, no explanatory text
2. No markdown code blocks (no ```json)
3. Use null for truly missing fields
4. Convert ALL percentages to numeric decimals
5. Arrays MUST be populated if data exists in HTML
6. Validate: holdings array should have 5-15 items
7. Validate: sector_allocation array should have 8-15 items
8. Validate: country_allocation array should have 1-15 items

HTML page content:
{html_truncated}
"""

        logger.info("Sending HTML to Gemini for extraction...")
        response = agent.generate_content(
            prompt=prompt,
            stream=False,
        )

        if not response:
            logger.error("No response from AI")
            return None

        # OpenAI may return parsed dict directly or as text
        if isinstance(response, dict):
            logger.info("✓ Received parsed dict response from AI")
            return response

        # Extract text from response
        text = response.text if hasattr(response, "text") else str(response)
        logger.info(f"Received response ({len(text)} chars)")

        # Try parsing as JSON
        try:
            import re

            # Remove markdown code blocks if present
            text = re.sub(r"```json\s*", "", text)
            text = re.sub(r"```\s*", "", text)
            text = text.strip()

            data = json.loads(text)
            logger.info("✓ Successfully parsed JSON response from AI")
            return data
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON: {e}")
            logger.error(f"Raw response (first 500 chars): {text[:500]}")

            # Try eval as fallback for Python dict string (single quotes)
            try:
                import ast

                data = ast.literal_eval(text)
                logger.info("✓ Successfully parsed Python dict response from AI")
                return data
            except Exception as eval_error:
                logger.error(f"Failed to parse as Python dict: {eval_error}")
                return None

    except Exception as e:
        logger.error(f"Failed to extract data with AI: {e}")
        return None


def validate_extracted_data(data: dict[str, Any]) -> bool:
    """
    Validate that extracted data meets minimum requirements.

    Args:
        data: Extracted ETF data

    Returns:
        True if valid, False otherwise
    """
    critical_fields = ["name", "isin"]
    recommended_fields = ["ter_percent", "fund_provider"]
    array_fields = ["holdings", "sector_allocation", "country_allocation"]

    validation_passed = True
    warnings = []

    # Check critical fields (must be present and non-null)
    for field in critical_fields:
        if field not in data or data[field] is None:
            logger.error(f"CRITICAL: Missing required field: {field}")
            validation_passed = False

    # Check recommended fields (warn if missing)
    for field in recommended_fields:
        if field not in data or data[field] is None:
            warnings.append(f"Missing recommended field: {field}")

    # Check array fields exist (can be empty)
    for field in array_fields:
        if field not in data or not isinstance(data[field], list):
            logger.error(f"CRITICAL: Field '{field}' must be an array")
            validation_passed = False
        elif len(data[field]) == 0:
            warnings.append(f"Array '{field}' is empty")

    # Log warnings
    for warning in warnings:
        logger.warning(warning)

    if validation_passed:
        if warnings:
            logger.info("✓ Data validation passed with warnings")
        else:
            logger.info("✓ Data validation passed perfectly")

    return validation_passed


def main():
    parser = argparse.ArgumentParser(description="Test ETF scraper with justetf.com")
    parser.add_argument("url", help="justetf.com ETF profile URL")
    parser.add_argument("--debug", action="store_true", help="Save HTML to file for debugging")
    parser.add_argument("--save-to-db", action="store_true", help="Save extracted data to database")
    args = parser.parse_args()

    logger.info("=" * 80)
    logger.info("ETF Scraper Validation Script - Phase 0")
    logger.info("=" * 80)

    # Step 1: Extract ISIN
    logger.info("\n[1/4] Extracting ISIN from URL...")
    isin = extract_isin_from_url(args.url)
    if not isin:
        logger.error("Failed to extract ISIN from URL")
        sys.exit(1)

    # Step 2: Fetch page HTML
    logger.info("\n[2/4] Fetching page HTML with Playwright...")
    html = fetch_etf_page(args.url, debug=args.debug)
    if not html:
        logger.error("Failed to fetch page HTML")
        sys.exit(1)

    if len(html) < 100000:
        logger.warning(f"HTML size ({len(html)} chars) is smaller than typical (>100k)")

    # Step 3: Extract data with AI
    logger.info("\n[3/4] Extracting ETF data with Gemini AI...")
    etf_data = extract_etf_data_with_ai(html, isin)
    if not etf_data:
        logger.error("Failed to extract ETF data")
        sys.exit(1)

    # Step 4: Validate and display results
    logger.info("\n[4/4] Validating extracted data...")
    logger.info(f"Extracted data preview: {json.dumps(etf_data, indent=2)[:500]}")
    if not validate_extracted_data(etf_data):
        logger.error("Data validation failed")
        logger.error(f"Full extracted data: {json.dumps(etf_data, indent=2)}")
        sys.exit(1)

    # Display results
    logger.info("\n" + "=" * 80)
    logger.info("EXTRACTED ETF DATA")
    logger.info("=" * 80)
    print(json.dumps(etf_data, indent=2))

    # Save to database if requested
    if args.save_to_db:
        logger.info("\n" + "=" * 80)
        logger.info("SAVING TO DATABASE")
        logger.info("=" * 80)
        try:
            from connectors.etf_fundamental import ETFFundamentalConnector

            connector = ETFFundamentalConnector()
            result = connector.upsert(etf_data)
            logger.info(f"✓ Saved to database: {result.isin} - {result.name}")
        except Exception as e:
            logger.error(f"Failed to save to database: {e}")
            sys.exit(1)

    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("SUMMARY")
    logger.info("=" * 80)
    logger.info(f"✓ ISIN: {etf_data.get('isin')}")
    logger.info(f"✓ Name: {etf_data.get('name')}")
    logger.info(f"✓ Holdings: {len(etf_data.get('holdings', []))} items")
    logger.info(f"✓ Sectors: {len(etf_data.get('sector_allocation', []))} items")
    logger.info(f"✓ Countries: {len(etf_data.get('country_allocation', []))} items")
    logger.info(f"✓ TER: {etf_data.get('ter_percent')}%")
    if args.save_to_db:
        logger.info("✓ Data saved to database")
    logger.info("\n🎉 Phase 0 validation SUCCESSFUL!")


if __name__ == "__main__":
    main()
