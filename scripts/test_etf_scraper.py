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
import sys
from typing import Any
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright

from agent.agent import Agent

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


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
            page.goto(url, timeout=30000)

            # Wait for page to load
            logger.info("Waiting for page load...")
            page.wait_for_load_state("networkidle", timeout=30000)
            page.wait_for_timeout(3000)

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
                    page.wait_for_selector(selector, timeout=5000)
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
            page.wait_for_timeout(3000)

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
        logger.info("Initializing OpenAI...")
        agent = Agent(model_type="openai")

        # Truncate HTML to fit AI context window (OpenAI rate limits)
        # Balance between coverage and token limits (~200k tokens max for safety)
        html_truncated = html[:800000]
        logger.info(f"Using {len(html_truncated)} characters of HTML for AI extraction (full HTML: {len(html)} chars)")

        prompt = f"""Extract ETF data from the HTML page below and format it as a JSON object.

Follow these strict instructions:

1. Extract the ETF name - usually in an <h1> tag or prominent heading
2. Extract ISIN: {isin}
3. Extract ticker symbol if available
4. Extract fund size/AUM - look for "Fund size", "AUM", or "Assets" (convert to millions, e.g., "€55.5bn" becomes 55500)
5. Extract TER (Total Expense Ratio) - look for "TER", "Ongoing charges", or "Expense ratio" (as decimal percentage, e.g., "0.07%" becomes 0.07)
6. Extract replication method - look for "Replication", "Replication method" (e.g., "Physical", "Synthetic")
7. Extract distribution policy - look for "Distribution", "Use of income" (e.g., "Accumulating", "Distributing")
8. Extract fund currency - look for "Fund currency" or currency symbol
9. Extract domicile - look for "Domicile", "Fund domicile" (country code like "IE", "LU")
10. Extract launch date - look for "Inception", "Launch date" (format as YYYY-MM-DD)
11. Extract index tracked - look for "Index", "Tracks"
12. Extract fund provider - look for "Provider", "Fund provider", "Issuer" (e.g., "iShares", "Vanguard")

13. Extract top holdings from tables/lists showing company holdings:
    - Look for sections like "Holdings", "Top 10 holdings", "Portfolio composition"
    - Each holding should have: company name and weight percentage
    - Extract at least 5-10 holdings if available
    - Convert percentages to numbers (e.g., "7.04%" becomes 7.04)

14. Extract sector allocation:
    - Look for sections like "Sector allocation", "Sector breakdown"
    - Each sector should have: sector name and weight percentage
    - Extract all sectors shown

15. Extract country allocation:
    - Look for sections like "Country allocation", "Geographic breakdown"
    - Each country should have: country name and weight percentage
    - Extract all countries shown

Output format (JSON object only, no explanation, no markdown):
{{
  "name": "string",
  "isin": "{isin}",
  "ticker": "string or null",
  "fund_size_millions": number or null,
  "ter_percent": number or null,
  "replication_method": "string or null",
  "distribution_policy": "string or null",
  "fund_currency": "string or null",
  "domicile": "string or null",
  "launch_date": "YYYY-MM-DD or null",
  "index_tracked": "string or null",
  "fund_provider": "string or null",
  "holdings": [{{"name": "string", "weight_percent": number}}],
  "sector_allocation": [{{"sector": "string", "weight_percent": number}}],
  "country_allocation": [{{"country": "string", "weight_percent": number}}]
}}

Use null for missing fields. Use empty arrays [] if no holdings/sectors/countries found.

HTML page content:
{html_truncated}
"""

        logger.info("Sending HTML to OpenAI for extraction...")
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
    logger.info("\n🎉 Phase 0 validation SUCCESSFUL!")


if __name__ == "__main__":
    main()
