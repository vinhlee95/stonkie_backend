"""
Financial data crawler Celery tasks.
"""

import logging
import re
from typing import Optional, Tuple

from celery import Task
from playwright.sync_api import sync_playwright
from sqlalchemy.exc import IntegrityError

from agent.agent import Agent
from celery_app import celery_app
from connectors.cache import set_task_state
from connectors.database import get_db
from models.company_financial_statement import CompanyFinancialStatement

logger = logging.getLogger(__name__)


class CallbackTask(Task):
    """
    Custom task class that adds callback support for task lifecycle events.
    """

    def on_success(self, retval, task_id, args, kwargs):
        """Called when task succeeds."""
        logger.info(f"Task {task_id} completed successfully with result: {retval}")

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Called when task fails."""
        logger.error(f"Task {task_id} failed with error: {exc}")

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        """Called when task is retried."""
        logger.warning(f"Task {task_id} is being retried due to: {exc}")


@celery_app.task(base=CallbackTask, bind=True, name="tasks.test_task", max_retries=3, default_retry_delay=60)
def test_task(self, message: str = "Hello from Celery!") -> dict:
    """
    Simple test task to verify Celery is working correctly.

    Args:
        message: Message to print and return

    Returns:
        dict with status and message
    """
    try:
        logger.info(f"🎉 Test task started with message: {message}")
        print(f"🎉 Test task executing: {message}")

        # Simulate some work
        import time

        time.sleep(2)

        result = {"status": "success", "message": message, "task_id": self.request.id}

        logger.info("✅ Test task completed successfully")
        print(f"✅ Test task completed: {result}")

        return result

    except Exception as e:
        logger.error(f"❌ Test task failed: {e}")
        # Retry the task with exponential backoff
        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))


@celery_app.task(
    base=CallbackTask, bind=True, name="tasks.crawl_financial_data", max_retries=3, default_retry_delay=300
)
def crawl_financial_data_task(
    self, ticker: str, report_type: Optional[str] = None, period_type: Optional[str] = None
) -> dict:
    """
    Celery task to crawl financial data for a company.

    Args:
        ticker: Company ticker symbol
        report_type: Type of financial report (income_statement, balance_sheet, cash_flow)
        period_type: Period type (annually, quarterly)

    Returns:
        dict with status and details
    """
    try:
        logger.info(f"🚀 Starting financial data crawl for {ticker}")
        print(f"🚀 Crawling financial data for {ticker} (report_type={report_type}, period_type={period_type})")

        # TODO: Implement actual crawling logic
        # For now, just simulate the task
        import time

        time.sleep(5)

        result = {
            "status": "success",
            "ticker": ticker.upper(),
            "report_type": report_type,
            "period_type": period_type,
            "task_id": self.request.id,
            "message": f"Successfully crawled data for {ticker}",
        }

        logger.info(f"✅ Financial data crawl completed for {ticker}")
        print(f"✅ Crawl completed for {ticker}: {result}")

        return result

    except Exception as e:
        logger.error(f"❌ Financial data crawl failed for {ticker}: {e}")
        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))


# Helper functions for annual financial data crawling


def _save_to_database(ticker: str, statement_type: str, data: list) -> bool:
    """
    Save financial data to the database with concurrency safety.

    Args:
        ticker: Company ticker symbol
        statement_type: Type of statement (income_statement, balance_sheet, cash_flow)
        data: List of financial data dictionaries

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        db = next(get_db())

        # Find the most recent non-TTM year
        most_recent_year = None
        for item in data:
            if isinstance(item["period_end_year"], int):
                if most_recent_year is None or item["period_end_year"] > most_recent_year:
                    most_recent_year = item["period_end_year"]

        # Process each item
        for item in data:
            period_end_year = item["period_end_year"]
            is_ttm = period_end_year == "TTM"

            # If it's TTM, use the most recent year + 1
            if is_ttm and most_recent_year is not None:
                period_end_year = most_recent_year + 1

            # Use atomic upsert with retry logic for better concurrency safety
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    # Try to get existing record with SELECT FOR UPDATE to lock it
                    existing_record = (
                        db.query(CompanyFinancialStatement)
                        .filter(
                            CompanyFinancialStatement.company_symbol == ticker.upper(),
                            CompanyFinancialStatement.period_end_year == period_end_year,
                        )
                        .with_for_update(nowait=False)
                        .first()
                    )

                    if existing_record:
                        # Check if the specific field for this statement type is already populated
                        field_already_populated = False
                        if statement_type == "income_statement" and existing_record.income_statement is not None:
                            field_already_populated = True
                        elif statement_type == "balance_sheet" and existing_record.balance_sheet is not None:
                            field_already_populated = True
                        elif statement_type == "cash_flow" and existing_record.cash_flow is not None:
                            field_already_populated = True

                        if field_already_populated:
                            logger.info(
                                f"Skipping existing record for {ticker} {statement_type} {period_end_year} - already populated"
                            )
                            break

                        logger.info(f"Updating existing record for {ticker} {statement_type} {period_end_year}")
                        # Update existing record
                        if statement_type == "income_statement":
                            existing_record.income_statement = item["metrics"]
                        elif statement_type == "balance_sheet":
                            existing_record.balance_sheet = item["metrics"]
                        elif statement_type == "cash_flow":
                            existing_record.cash_flow = item["metrics"]
                        existing_record.is_ttm = is_ttm
                    else:
                        logger.info(f"Creating new record for {ticker} {statement_type} {period_end_year}")
                        # Create new record with only the current statement type
                        record = CompanyFinancialStatement(
                            company_symbol=ticker.upper(),
                            period_end_year=period_end_year,
                            is_ttm=is_ttm,
                        )

                        # Set the appropriate statement type
                        if statement_type == "income_statement":
                            record.income_statement = item["metrics"]
                        elif statement_type == "balance_sheet":
                            record.balance_sheet = item["metrics"]
                        elif statement_type == "cash_flow":
                            record.cash_flow = item["metrics"]

                        db.add(record)

                    # Commit the transaction
                    db.commit()
                    break  # Success, exit retry loop

                except IntegrityError as e:
                    # Handle race condition where another process created the record
                    db.rollback()
                    if attempt < max_retries - 1:
                        logger.warning(f"Integrity error on attempt {attempt + 1}, retrying... {e}")
                        continue
                    else:
                        logger.error(f"Failed after {max_retries} attempts due to integrity error: {e}")
                        break
                except Exception as e:
                    db.rollback()
                    if attempt < max_retries - 1:
                        logger.warning(f"Database error on attempt {attempt + 1}, retrying... {e}")
                        continue
                    else:
                        raise e

        logger.info(f"✅ Financial data for {ticker} {statement_type} saved to database")
        db.close()
        return True

    except Exception as e:
        logger.error(f"Failed to save financial data to database: {e}")
        if "db" in locals():
            db.rollback()
            db.close()
        return False


def _extract_financial_data_from_page(url: str) -> Optional[Tuple[str, list]]:
    """
    Extract financial data from Yahoo Finance page using Playwright.

    Args:
        url: Yahoo Finance URL to scrape

    Returns:
        Tuple of (body_html, periods) or None if failed
    """
    max_browser_restarts = 2

    for browser_attempt in range(max_browser_restarts + 1):
        try:
            if browser_attempt > 0:
                logger.info(f"Browser restart attempt {browser_attempt}/{max_browser_restarts} for URL: {url}")

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)

                # Create a fresh incognito-like context
                context = browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    java_script_enabled=True,
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    ignore_https_errors=True,
                    locale="en-US",
                    storage_state=None,
                )
                page = context.new_page()

                # Navigate to the page
                logger.info(f"Navigating to: {url}")
                page.goto(url, timeout=10000)

                # Wait for page to load
                page.wait_for_load_state("networkidle", timeout=10000)
                page.wait_for_timeout(5000)

                # Handle cookie banner
                try:
                    page.wait_for_selector(".accept-all", timeout=5000)
                    page.click(".accept-all")
                    logger.info("Accepted cookies")
                    page.wait_for_timeout(2000)
                except Exception as e:
                    logger.info(f"No cookie banner found or already accepted: {str(e)}")

                # Wait for financial table structure
                logger.info("Waiting for financial table structure...")
                try:
                    page.wait_for_function(
                        """
                        () => {
                            const tableHeader = document.querySelector('div[class*="tableHeader"]');
                            const tableBody = document.querySelector('div[class*="tableBody"]');
                            const expandButton = document.querySelector('span.expand');
                            return tableHeader && tableBody && expandButton;
                        }
                        """,
                        timeout=10000,
                    )
                    logger.info("Financial table structure is ready")
                except Exception as e:
                    logger.warning(f"Financial table structure not fully loaded, proceeding anyway... {str(e)}")

                # Click expand button
                logger.info("Clicking expand button...")
                expand_button = page.locator("span.expand")
                expand_button.wait_for(state="visible", timeout=10000)
                expand_button.scroll_into_view_if_needed()

                # Try different click methods
                expand_clicked = False
                for attempt in range(3):
                    try:
                        if attempt == 0:
                            expand_button.click(force=True)
                        elif attempt == 1:
                            page.evaluate('document.querySelector("span.expand").click()')
                        else:
                            expand_button.dispatch_event("click")

                        page.wait_for_timeout(3000)
                        expand_clicked = True
                        logger.info(f"Expand button clicked successfully after {attempt + 1} attempts")
                        break
                    except Exception as click_error:
                        logger.warning(f"Attempt {attempt + 1}: Failed to click expand button: {click_error}")
                        if attempt < 2:
                            page.wait_for_timeout(2000)

                if not expand_clicked:
                    logger.error(f"Failed to click expand button on browser attempt {browser_attempt + 1}")
                    browser.close()
                    if browser_attempt < max_browser_restarts:
                        continue
                    else:
                        return None

                # Wait for content to load after expand
                page.wait_for_timeout(5000)

                # Extract table data
                logger.info("Extracting table data...")
                table_header = page.locator('div[class*="tableHeader"]')
                table_body = page.locator('div[class*="tableBody"]')

                header_html = table_header.inner_html(timeout=15000)
                body_html = table_body.inner_html(timeout=15000)

                # Extract periods
                periods = re.findall(r">([^<]+)<\/div>", header_html)
                periods = [p.strip() for p in periods]
                periods = [p for p in periods if p != "Breakdown" and p != ""]
                logger.info(f"Extracted periods: {periods}")

                browser.close()
                logger.info(f"Successfully extracted data on browser attempt {browser_attempt + 1}")
                return (body_html, periods)

        except Exception as e:
            logger.error(f"Error on browser attempt {browser_attempt + 1}: {e}")
            if "browser" in locals():
                try:
                    browser.close()
                except Exception:
                    pass

            if browser_attempt < max_browser_restarts:
                continue
            else:
                logger.error(f"Failed after {max_browser_restarts + 1} browser attempts")
                return None

    return None


@celery_app.task(
    base=CallbackTask, bind=True, name="tasks.crawl_annual_financial_data", max_retries=3, default_retry_delay=300
)
def crawl_annual_financial_data_task(self, ticker: str, statement_type: str) -> dict:
    """
    Celery task to crawl annual financial data from Yahoo Finance.

    Args:
        ticker: Company ticker symbol
        statement_type: Type of statement (income_statement, balance_sheet, cash_flow)

    Returns:
        dict with status and details
    """
    try:
        # Update task state to running
        set_task_state(
            ticker=ticker, report_type=statement_type, status="running", task_id=self.request.id, period_type="annually"
        )

        logger.info(f"🚀 Starting annual financial data crawl for {ticker} - {statement_type}")

        # Map statement type to Yahoo Finance URL
        base_url = f"https://finance.yahoo.com/quote/{ticker.upper()}"
        url_mapping = {
            "income_statement": f"{base_url}/financials/",
            "balance_sheet": f"{base_url}/balance-sheet/",
            "cash_flow": f"{base_url}/cash-flow/",
        }

        url = url_mapping.get(statement_type)
        if not url:
            raise ValueError(f"Invalid statement_type: {statement_type}")

        # Extract data from page
        logger.info(f"Extracting data from: {url}")
        result = _extract_financial_data_from_page(url)

        if result is None:
            raise Exception(f"Failed to extract data from {url}")

        table_body_html, periods = result
        logger.info(f"Extracted periods: {periods}")

        # Use OpenAI to parse the HTML table
        logger.info("Parsing HTML with OpenAI...")
        openai_agent = Agent(model_type="openai")

        prompt = f"""
            Extract financial data from the following HTML table and format it as a JSON list.
            Each object in the list must represent a single period and have the following structure:
            {{
                "period_end_year": number,
                "metrics": {{"metric_name": number}}
            }}

            Follow these strict instructions:

            - The periods are: {periods}.
            - Each period object must contain a metrics object.
            - The table rows are represented by <div> elements with class "row". Inside each row:
                - The metric name is located in a child <div> with class "rowTitle".
                - The values for the periods are in child <div> elements with class "column" (excluding the one containing the title).
            - You must include every metric found in a rowTitle element exactly as it appears in the text (including symbols, spacing, and casing). Do not skip or merge similar rows. Treat duplicate names as separate metrics if they appear as distinct rows in the HTML.
            - For each metric, extract its corresponding values from the following "column" elements. They are ordered left to right and align with the period order provided.
            - Clean each numerical value as follows:
                - Remove any commas.
                - Convert the string to a number.
                - If the value is exactly "--", omit that metric entirely from the corresponding period's metrics. Do not include it with null or zero.
            - Even if a metric has -- for all periods, still include its name and row position when processing. It may have valid data in the future or in other contexts.
            - Do not infer or assume any data. Only extract what is explicitly present in the provided HTML.
            - For period_end_year, extract the year from the date (e.g., "12/31/2024" becomes 2024). If the period is "TTM", use "TTM" as the period_end_year.
            - The final output must be a JSON array only (no explanation, no code block markers). Example output:
            [
                {{
                    "period_end_year": "TTM",
                    "metrics": {{
                        "Revenue": 1000000,
                        "Net Income": 500000
                    }}
                }},
                {{
                    "period_end_year": 2024,
                    "metrics": {{
                        "Revenue": 900000,
                        "Net Income": 450000
                    }}
                }}
            ]

            Now process the following HTML table:
            {table_body_html}
        """

        json_response = openai_agent.generate_content(prompt=prompt, stream=False)

        if not json_response:
            raise Exception("No data received from OpenAI model")

        logger.info(f"Successfully extracted {len(json_response)} periods of financial data")

        # Save to database
        success = _save_to_database(ticker, statement_type, json_response)

        if not success:
            raise Exception("Failed to save data to database")

        result = {
            "status": "success",
            "ticker": ticker.upper(),
            "statement_type": statement_type,
            "periods_count": len(json_response),
            "task_id": self.request.id,
            "message": f"Successfully crawled and saved {statement_type} for {ticker}",
        }

        # Update task state to completed
        set_task_state(
            ticker=ticker,
            report_type=statement_type,
            status="completed",
            task_id=self.request.id,
            period_type="annually",
        )

        logger.info(f"✅ Annual financial data crawl completed for {ticker} - {statement_type}")
        return result

    except Exception as e:
        logger.error(f"❌ Annual financial data crawl failed for {ticker} - {statement_type}: {e}")

        # Update task state to failed
        set_task_state(
            ticker=ticker,
            report_type=statement_type,
            status="failed",
            task_id=self.request.id,
            period_type="annually",
            error=str(e),
        )

        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))


def _extract_quarterly_financial_data_from_page(url: str) -> Optional[Tuple[str, list]]:
    """
    Extract quarterly financial data from Yahoo Finance page using Playwright.
    Clicks the "Quarterly" tab before extracting.

    Returns:
        Tuple of (body_html, periods) or None if failed
    """
    max_browser_restarts = 2

    for browser_attempt in range(max_browser_restarts + 1):
        try:
            if browser_attempt > 0:
                logger.info(f"Browser restart attempt {browser_attempt}/{max_browser_restarts} for URL: {url}")

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    java_script_enabled=True,
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    ignore_https_errors=True,
                    locale="en-US",
                    storage_state=None,
                )
                page = context.new_page()

                logger.info(f"Navigating to: {url}")
                page.goto(url, timeout=10000)
                page.wait_for_load_state("networkidle", timeout=10000)
                page.wait_for_timeout(5000)

                # Handle cookie banner
                try:
                    page.wait_for_selector(".accept-all", timeout=5000)
                    page.click(".accept-all")
                    logger.info("Accepted cookies")
                    page.wait_for_timeout(2000)
                except Exception:
                    logger.info("No cookie banner found or already accepted")

                # Wait for financial table + quarterly tab
                try:
                    page.wait_for_function(
                        """
                        () => {
                            const tableHeader = document.querySelector('div[class*="tableHeader"]');
                            const tableBody = document.querySelector('div[class*="tableBody"]');
                            const tabs = document.querySelector('button#tab-quarterly');
                            return tableHeader && tableBody && tabs;
                        }
                        """,
                        timeout=10000,
                    )
                except Exception:
                    logger.warning("Financial table structure not fully loaded, proceeding anyway...")

                # Click quarterly tab
                tab_quarterly = page.locator("button#tab-quarterly")
                tab_quarterly.wait_for(state="visible", timeout=10000)
                tab_quarterly.scroll_into_view_if_needed()

                quarterly_selected = False
                aria_selected = tab_quarterly.get_attribute("aria-selected")
                if aria_selected == "true":
                    quarterly_selected = True
                else:
                    for attempt in range(3):
                        try:
                            if attempt == 0:
                                tab_quarterly.click(force=True)
                            elif attempt == 1:
                                page.evaluate('document.querySelector("#tab-quarterly").click()')
                            else:
                                tab_quarterly.dispatch_event("click")
                            page.wait_for_timeout(3000)
                            if tab_quarterly.get_attribute("aria-selected") == "true":
                                quarterly_selected = True
                                break
                        except Exception:
                            if attempt < 2:
                                page.wait_for_timeout(2000)

                if not quarterly_selected:
                    logger.error(f"Failed to select quarterly tab on browser attempt {browser_attempt + 1}")
                    browser.close()
                    if browser_attempt < max_browser_restarts:
                        continue
                    return None

                # Wait for quarterly content to load
                page.wait_for_timeout(5000)

                # Click expand button if present
                try:
                    expand_button = page.locator("span.expand")
                    if expand_button.count() > 0:
                        expand_button.wait_for(state="visible", timeout=5000)
                        expand_button.scroll_into_view_if_needed()
                        expand_button.click(force=True)
                        page.wait_for_timeout(3000)
                except Exception:
                    logger.info("No expand button found or already expanded")

                # Extract table data
                table_header = page.locator('div[class*="tableHeader"]')
                table_body = page.locator('div[class*="tableBody"]')

                header_html = table_header.inner_html(timeout=15000)
                body_html = table_body.inner_html(timeout=15000)

                periods = re.findall(r">([^<]+)<\/div>", header_html)
                periods = [p.strip() for p in periods]
                periods = [p for p in periods if p != "Breakdown" and p != ""]
                logger.info(f"Extracted quarterly periods: {periods}")

                browser.close()
                return (body_html, periods)

        except Exception as e:
            logger.error(f"Error on browser attempt {browser_attempt + 1}: {e}")
            if "browser" in locals():
                try:
                    browser.close()
                except Exception:
                    pass
            if browser_attempt < max_browser_restarts:
                continue
            return None

    return None


def _save_quarterly_to_database(ticker: str, statement_type: str, data: list) -> bool:
    """Save quarterly financial data to the database."""
    from models.company_quarterly_financial_statement import CompanyQuarterlyFinancialStatement

    try:
        db = next(get_db())

        for item in data:
            period_end_quarter = item["period_end_quarter"]

            max_retries = 3
            for attempt in range(max_retries):
                try:
                    existing_record = (
                        db.query(CompanyQuarterlyFinancialStatement)
                        .filter(
                            CompanyQuarterlyFinancialStatement.company_symbol == ticker.upper(),
                            CompanyQuarterlyFinancialStatement.period_end_quarter == period_end_quarter,
                        )
                        .with_for_update(nowait=False)
                        .first()
                    )

                    if existing_record:
                        field_already_populated = False
                        if statement_type == "income_statement" and existing_record.income_statement is not None:
                            field_already_populated = True
                        elif statement_type == "balance_sheet" and existing_record.balance_sheet is not None:
                            field_already_populated = True
                        elif statement_type == "cash_flow" and existing_record.cash_flow is not None:
                            field_already_populated = True

                        if field_already_populated:
                            logger.info(
                                f"Skipping existing quarterly record for {ticker} {statement_type} {period_end_quarter}"
                            )
                            break

                        logger.info(
                            f"Updating existing quarterly record for {ticker} {statement_type} {period_end_quarter}"
                        )
                        if statement_type == "income_statement":
                            existing_record.income_statement = item["metrics"]
                        elif statement_type == "balance_sheet":
                            existing_record.balance_sheet = item["metrics"]
                        elif statement_type == "cash_flow":
                            existing_record.cash_flow = item["metrics"]
                    else:
                        logger.info(f"Creating new quarterly record for {ticker} {statement_type} {period_end_quarter}")
                        record = CompanyQuarterlyFinancialStatement(
                            company_symbol=ticker.upper(),
                            period_end_quarter=period_end_quarter,
                        )
                        if statement_type == "income_statement":
                            record.income_statement = item["metrics"]
                        elif statement_type == "balance_sheet":
                            record.balance_sheet = item["metrics"]
                        elif statement_type == "cash_flow":
                            record.cash_flow = item["metrics"]
                        db.add(record)

                    db.commit()
                    break

                except IntegrityError as e:
                    db.rollback()
                    if attempt < max_retries - 1:
                        logger.warning(f"Integrity error on attempt {attempt + 1}, retrying... {e}")
                        continue
                    else:
                        logger.error(f"Failed after {max_retries} attempts due to integrity error: {e}")
                        break
                except Exception as e:
                    db.rollback()
                    if attempt < max_retries - 1:
                        logger.warning(f"Database error on attempt {attempt + 1}, retrying... {e}")
                        continue
                    else:
                        raise e

        logger.info(f"Quarterly financial data for {ticker} {statement_type} saved to database")
        db.close()
        return True

    except Exception as e:
        logger.error(f"Failed to save quarterly financial data to database: {e}")
        if "db" in locals():
            db.rollback()
            db.close()
        return False


@celery_app.task(
    base=CallbackTask, bind=True, name="tasks.crawl_quarterly_financial_data", max_retries=3, default_retry_delay=300
)
def crawl_quarterly_financial_data_task(self, ticker: str, statement_type: str) -> dict:
    """
    Celery task to crawl quarterly financial data from Yahoo Finance.

    Args:
        ticker: Company ticker symbol
        statement_type: Type of statement (income_statement, balance_sheet, cash_flow)

    Returns:
        dict with status and details
    """
    try:
        set_task_state(
            ticker=ticker,
            report_type=statement_type,
            status="running",
            task_id=self.request.id,
            period_type="quarterly",
        )

        logger.info(f"Starting quarterly financial data crawl for {ticker} - {statement_type}")

        base_url = f"https://finance.yahoo.com/quote/{ticker.upper()}"
        url_mapping = {
            "income_statement": f"{base_url}/financials/",
            "balance_sheet": f"{base_url}/balance-sheet/",
            "cash_flow": f"{base_url}/cash-flow/",
        }

        url = url_mapping.get(statement_type)
        if not url:
            raise ValueError(f"Invalid statement_type: {statement_type}")

        result = _extract_quarterly_financial_data_from_page(url)
        if result is None:
            raise Exception(f"Failed to extract quarterly data from {url}")

        table_body_html, periods = result

        openai_agent = Agent(model_type="openai")
        prompt = f"""
            Extract financial data from the following HTML table and format it as a JSON list.
            Each object in the list must represent a single period and have the following structure:
            {{
                "period_end_quarter": "string",
                "metrics": {{"metric_name": number}}
            }}

            Follow these strict instructions:

            - The periods are: {periods}.
            - Each period object must contain a metrics object.
            - The table rows are represented by <div> elements with class "row". Inside each row:
                - The metric name is located in a child <div> with class "rowTitle".
                - The values for the periods are in child <div> elements with class "column" (excluding the one containing the title).
            - You must include every metric found in a rowTitle element exactly as it appears in the text (including symbols, spacing, and casing). Do not skip or merge similar rows. Treat duplicate names as separate metrics if they appear as distinct rows in the HTML.
            - For each metric, extract its corresponding values from the following "column" elements. They are ordered left to right and align with the period order provided.
            - Clean each numerical value as follows:
                - Remove any commas.
                - Convert the string to a number.
                - If the value is exactly "--", omit that metric entirely from the corresponding period's metrics. Do not include it with null or zero.
            - Do not infer or assume any data. Only extract what is explicitly present in the provided HTML.
            - The final output must be a JSON array only (no explanation, no code block markers). Example output:
            [
                {{
                    "period_end_quarter": "3/31/2025",
                    "metrics": {{
                        "Revenue": 1000000,
                        "Net Income": 500000
                    }}
                }},
                {{
                    "period_end_quarter": "12/31/2024",
                    "metrics": {{
                        "Revenue": 900000,
                        "Net Income": 450000
                    }}
                }}
            ]

            Now process the following HTML table:
            {table_body_html}
        """

        json_response = openai_agent.generate_content(prompt=prompt, stream=False)
        if not json_response:
            raise Exception("No data received from OpenAI model")

        # Filter out TTM period if present
        data_to_save = [item for item in json_response if item.get("period_end_quarter") != "TTM"]

        logger.info(f"Successfully extracted {len(data_to_save)} quarters of financial data")

        success = _save_quarterly_to_database(ticker, statement_type, data_to_save)
        if not success:
            raise Exception("Failed to save quarterly data to database")

        result = {
            "status": "success",
            "ticker": ticker.upper(),
            "statement_type": statement_type,
            "periods_count": len(data_to_save),
            "task_id": self.request.id,
            "message": f"Successfully crawled and saved quarterly {statement_type} for {ticker}",
        }

        set_task_state(
            ticker=ticker,
            report_type=statement_type,
            status="completed",
            task_id=self.request.id,
            period_type="quarterly",
        )

        logger.info(f"Quarterly financial data crawl completed for {ticker} - {statement_type}")
        return result

    except Exception as e:
        logger.error(f"Quarterly financial data crawl failed for {ticker} - {statement_type}: {e}")

        set_task_state(
            ticker=ticker,
            report_type=statement_type,
            status="failed",
            task_id=self.request.id,
            period_type="quarterly",
            error=str(e),
        )

        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))
