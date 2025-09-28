"""
Export Annual Financial Reports for Tickers

This script exports financial data (income statements, balance sheets, and cash flow statements)
from Yahoo Finance for ticker symbols and saves them to the database.

Usage:
    # Export data for specific tickers
    python export_annual_financial_report.py --tickers=AAPL,TSLA,MSFT
    
    # Export data for all tickers in the database (default behavior when no --tickers specified)
    python export_annual_financial_report.py

Examples:
    python export_annual_financial_report.py --tickers=AAPL
    python export_annual_financial_report.py --tickers=AAPL,TSLA,GOOGL,MSFT
    python export_annual_financial_report.py  # Processes all tickers from database
"""

import sys
import argparse
from pathlib import Path

# Add the parent directory to the Python path
current_dir = Path(__file__).resolve().parent
parent_dir = current_dir.parent
sys.path.insert(0, str(parent_dir))

from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import re
import concurrent.futures
from sqlalchemy.exc import IntegrityError

from connectors.database import get_db
from connectors.company import CompanyConnector
from models.company_financial_statement import CompanyFinancialStatement
from agent.agent import Agent
from services.company import CompanyConnector

load_dotenv()

company_connector = CompanyConnector()

def save_to_database(ticker, statement_type, data):
    """
    Save financial data to the database with concurrency safety
    """
    try:
        db = next(get_db())
        
        # Find the most recent non-TTM year
        most_recent_year = None
        for item in data:
            if isinstance(item['period_end_year'], int):
                if most_recent_year is None or item['period_end_year'] > most_recent_year:
                    most_recent_year = item['period_end_year']
        
        # Process each item
        for item in data:
            period_end_year = item['period_end_year']
            is_ttm = period_end_year == 'TTM'
            
            # If it's TTM, use the most recent year + 1
            if is_ttm and most_recent_year is not None:
                period_end_year = most_recent_year + 1
            
            # Use atomic upsert with retry logic for better concurrency safety
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    # Try to get existing record with SELECT FOR UPDATE to lock it
                    existing_record = db.query(CompanyFinancialStatement).filter(
                        CompanyFinancialStatement.company_symbol == ticker.upper(),
                        CompanyFinancialStatement.period_end_year == period_end_year,
                    ).with_for_update(nowait=False).first()
                    
                    if existing_record:
                        # Check if the specific field for this statement type is already populated
                        field_already_populated = False
                        if statement_type == 'income_statement' and existing_record.income_statement is not None:
                            field_already_populated = True
                        elif statement_type == 'balance_sheet' and existing_record.balance_sheet is not None:
                            field_already_populated = True
                        elif statement_type == 'cash_flow' and existing_record.cash_flow is not None:
                            field_already_populated = True
                        
                        if field_already_populated:
                            print(f"🔄 Skipping existing record for {ticker} {statement_type} {period_end_year} because {statement_type} is already populated.")
                            break

                        print(f"🔄 Updating existing record for {ticker} {statement_type} {period_end_year}")
                        # Update existing record
                        if statement_type == 'income_statement':
                            existing_record.income_statement = item['metrics']
                        elif statement_type == 'balance_sheet':
                            existing_record.balance_sheet = item['metrics']
                        elif statement_type == 'cash_flow':
                            existing_record.cash_flow = item['metrics']
                        existing_record.is_ttm = is_ttm
                    else:
                        print(f"🔄 Creating new record for {ticker} {statement_type} {period_end_year}")
                        # Create new record with only the current statement type
                        record = CompanyFinancialStatement(
                            company_symbol=ticker.upper(),
                            period_end_year=period_end_year,
                            is_ttm=is_ttm,
                        )
                        
                        # Set the appropriate statement type
                        if statement_type == 'income_statement':
                            record.income_statement = item['metrics']
                        elif statement_type == 'balance_sheet':
                            record.balance_sheet = item['metrics']
                        elif statement_type == 'cash_flow':
                            record.cash_flow = item['metrics']
                        
                        db.add(record)
                    
                    # Commit the transaction
                    db.commit()
                    break  # Success, exit retry loop
                    
                except IntegrityError as e:
                    # Handle race condition where another process created the record
                    db.rollback()
                    if attempt < max_retries - 1:
                        print(f"🔄 Integrity error on attempt {attempt + 1}, retrying... {e}")
                        continue
                    else:
                        print(f"❌ Failed after {max_retries} attempts due to integrity error: {e}")
                        break
                except Exception as e:
                    db.rollback()
                    if attempt < max_retries - 1:
                        print(f"🔄 Database error on attempt {attempt + 1}, retrying... {e}")
                        continue
                    else:
                        raise e
        
        print(f"✅✅✅ Financial data for {ticker} {statement_type} has been saved to the database")
        
    except Exception as e:
        print(f"❌❌❌ Failed to save financial data to the database: {e}")
        db.rollback()
    finally:
        db.close()

def export_financial_data_to_text(url):
    """
    Export financial data with browser restart mechanism on failure
    """
    max_browser_restarts = 2  # Maximum number of full browser restarts
    
    for browser_attempt in range(max_browser_restarts + 1):
        try:
            if browser_attempt > 0:
                print(f"🔄 Browser restart attempt {browser_attempt}/{max_browser_restarts} for URL: {url}")
            
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)

                # Create a fresh incognito-like context
                context = browser.new_context(
                    viewport={'width': 1920, 'height': 1080},
                    java_script_enabled=True,
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    ignore_https_errors=True,
                    locale='en-US',
                    storage_state=None
                )
                page = context.new_page()
                
                # Navigate to the page with extended timeout
                print(f"🌐 Navigating to: {url}")
                page.goto(url, timeout=10000)
                
                # Wait for the page to be fully loaded including network activity
                print("⏳ Waiting for page to fully load...")
                
                page.wait_for_load_state('networkidle', timeout=10000)  # Wait until no network requests for 500ms
                
                print("⏳ Waiting for ads and background scripts to settle...")
                page.wait_for_timeout(5000)  # 5 second wait for ads/trackers
                
                # Handle cookie banner
                try:
                    page.wait_for_selector('.accept-all', timeout=10000)
                    page.click('.accept-all')
                    print("✅ Accepted cookies")
                    page.wait_for_timeout(2000)  # Wait after cookie acceptance
                except:
                    print("⚠️  No cookie banner found or already accepted")

                # Wait for the financial data table structure to be present
                print("⏳ Waiting for financial table structure...")
                try:
                    # Wait for either the table or a loading indicator
                    page.wait_for_function("""
                        () => {
                            // Check if table structure exists
                            const tableHeader = document.querySelector('div[class*="tableHeader"]');
                            const tableBody = document.querySelector('div[class*="tableBody"]');
                            const expandButton = document.querySelector('span.expand');
                            
                            return tableHeader && tableBody && expandButton;
                        }
                    """, timeout=10000)
                    print("✅ Financial table structure is ready")
                except:
                    print("⚠️  Financial table structure not fully loaded, proceeding anyway...")

                # Now proceed with expand button clicking
                print("🔄 Starting expand button click...")
                expand_button = page.locator('span.expand')
                expand_button.wait_for(state="visible", timeout=10000)
                expand_button.scroll_into_view_if_needed()
                
                # Try different click methods with retry
                max_click_attempts = 3
                expand_clicked = False
                
                for attempt in range(max_click_attempts):
                    try:
                        if attempt == 0:
                            expand_button.click(force=True)
                            print("🔄 Tried force click on expand button")
                        elif attempt == 1:
                            # Try JavaScript click
                            page.evaluate('document.querySelector("span.expand").click()')
                            print("🔄 Tried JavaScript click on expand button")
                        elif attempt == 2:
                            # Try dispatch event
                            expand_button.dispatch_event('click')
                            print("🔄 Tried dispatch event click on expand button")
                        
                        page.wait_for_timeout(3000)  # Wait to see if content expanded
                        expand_clicked = True
                        print(f"✅ Expand button clicked successfully after {attempt + 1} attempts")
                        break
                    except Exception as click_error:
                        print(f"⚠️  Attempt {attempt + 1}: Failed to click expand button: {click_error}")
                        if attempt < max_click_attempts - 1:
                            page.wait_for_timeout(2000)  # Wait between attempts
                
                if not expand_clicked:
                    print(f"❌ Failed to click expand button on browser attempt {browser_attempt + 1}")
                    browser.close()
                    
                    if browser_attempt < max_browser_restarts:
                        print(f"🔄 Restarting browser and retrying... (attempt {browser_attempt + 2}/{max_browser_restarts + 1})")
                        continue  # Restart browser
                    else:
                        print("❌❌❌ Failed to click expand button after all browser restart attempts")
                        return None
                
                # Wait for content to update after expand
                print("⏳ Waiting for expanded content to load...")
                page.wait_for_timeout(5000)  # Increased wait time for content refresh
                
                try:
                    # Wait for table content to be refreshed after expand
                    page.wait_for_function("""
                        () => {
                            const tableHeader = document.querySelector('div[class*="tableHeader"]');
                            const tableBody = document.querySelector('div[class*="tableBody"]');
                            
                            // Check if content exists and has actual data
                            return tableHeader && 
                                   tableBody && 
                                   tableHeader.innerHTML.length > 100 && 
                                   tableBody.innerHTML.length > 500;
                        }
                    """, timeout=10000)
                    print("✅ Table content is fully loaded and populated")
                except:
                    print("⚠️  Table content loading timeout, proceeding with current state...")
                
                # Final extraction with increased timeouts
                print("📊 Extracting table data...")
                table_header = page.locator('div[class*="tableHeader"]')
                table_body = page.locator('div[class*="tableBody"]')

                # Use longer timeouts for extraction
                header_html = table_header.inner_html(timeout=15000)
                body_html = table_body.inner_html(timeout=15000)

                # Extract periods for validation and return
                print("🔍 Extracting periods...")
                periods = re.findall(r'>([^<]+)<\/div>', header_html)
                periods = [p.strip() for p in periods]
                periods = [p for p in periods if p != 'Breakdown' and p != '']
                print(f"✅📘 Extracted periods: {periods}")

                browser.close()
                print(f"✅ Successfully extracted data on browser attempt {browser_attempt + 1}")
                return (body_html, periods)
                
        except Exception as e:
            print(f"❌ Error on browser attempt {browser_attempt + 1}: {e}")
            if 'browser' in locals():
                try:
                    browser.close()
                except:
                    pass
            
            if browser_attempt < max_browser_restarts:
                print(f"🔄 Retrying with fresh browser... (attempt {browser_attempt + 2}/{max_browser_restarts + 1})")
                continue
            else:
                print(f"❌❌❌ Failed after {max_browser_restarts + 1} browser attempts")
                return None
    
    return None

def export_financial_data_to_db(url, ticker, statement_type):
    """
    Export financial data from a URL to the database
    Returns True if successful, False otherwise
    """
    try:
        print(f"💲🗂️ Exporting annual financial data for {ticker} {statement_type} to database...")
        
        result = export_financial_data_to_text(url)
        if result is None:
            print(f"❌ Failed to extract data from {url}")
            return False
            
        table_body_html, periods = result

        print(f"✅📘 Using extracted periods: {periods}")
        print(f"✅📘 Done getting HTML content of the {statement_type}. Now feeding it to the model...")
        
        openai_agent = Agent(model_type="openai")

        final_prompt = f"""
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
                    "period_end_year": 'TTM',
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

        json_response = openai_agent.generate_content(prompt=final_prompt, stream=False)
        if json_response:
            print("✅✅✅ Successfully extracted financial data from the model to JSON")
            save_to_database(ticker, statement_type, json_response)
            return True
        else:
            print("❌❌❌ No data received from the model")
            return False
    except Exception as e:
        print(f"❌❌❌ Error in export_financial_data_to_db for {statement_type}: {e}")
        return False

def export_financial_data_worker(args):
    """
    Worker function for parallel execution
    """
    url, ticker, statement_type = args
    return export_financial_data_to_db(url, ticker, statement_type)

def persist_fundamental_data_worker(ticker):
    """
    Worker function for persisting fundamental data - runs once per ticker
    """
    try:
        company_connector.persist_fundamental_data(ticker)
        print(f"✅ Fundamental data for {ticker} has been ensured in the database")
        return True
    except Exception as e:
        print(f"❌ Failed to persist fundamental data for {ticker}: {e}")
        return False


def validate_ticker(ticker):
    """
    Validate ticker symbol format
    """
    if not ticker or len(ticker) > 10 or not ticker.isalnum():
        return False
    return True

def get_financial_urls(ticker):
    """
    Generate Yahoo Finance URLs for a given ticker symbol
    """
    base_url = f"https://finance.yahoo.com/quote/{ticker.upper()}"
    return (
        f"{base_url}/financials/",
        f"{base_url}/balance-sheet/",
        f"{base_url}/cash-flow/"
    )

def main():
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description='Export financial data for specified ticker symbols')
    parser.add_argument(
        '--tickers', 
        type=str, 
        required=False,
        help='Comma-separated list of ticker symbols (e.g., AAPL,TSLA,MSFT). If not provided, all tickers from database will be used.'
    )
    
    args = parser.parse_args()
    
    # Get ticker symbols
    if not args.tickers:
        # No tickers specified, fetch all from database
        print("🔍 No tickers specified, fetching all tickers from database...")
        company_fundamental_connector = CompanyConnector()
        tickers = company_fundamental_connector.get_all_company_tickers()
        if not tickers:
            print("❌ No tickers found in database")
            return
        print(f"📊 Found {len(tickers)} tickers in database")
    else:
        # Parse tickers from command line argument
        print(f"🎯 Using specified tickers: {args.tickers}")
        raw_tickers = [ticker.strip().upper() for ticker in args.tickers.split(',')]
        raw_tickers = [ticker for ticker in raw_tickers if ticker]  # Remove empty strings
        
        # Validate ticker symbols
        tickers = []
        invalid_tickers = []
        
        for ticker in raw_tickers:
            if validate_ticker(ticker):
                tickers.append(ticker)
            else:
                invalid_tickers.append(ticker)
        
        if invalid_tickers:
            print(f"⚠️  Invalid ticker symbols (skipped): {invalid_tickers}")
        
        if not tickers:
            print("❌ No valid tickers provided")
            print("Usage: python export_annual_financial_report.py --tickers=AAPL,TSLA,MSFT")
            print("       python export_annual_financial_report.py  (to fetch all tickers from database)")
            return
    
    print(f"🚀 Starting parallel export for {len(tickers)} tickers: {tickers}")
    
    # First, persist fundamental data for all tickers (once per ticker)
    print("📊 Step 1: Persisting fundamental data for all tickers...")
    fundamental_tasks = [(ticker, 'fundamental') for ticker in tickers]
    
    # Prepare financial data tasks for all tickers - each ticker has 3 statement types
    financial_tasks = []
    for ticker in tickers:
        financial_statement_url, balance_sheet_url, cash_flow_url = get_financial_urls(ticker)
        ticker_tasks = [
            (financial_statement_url, ticker, 'income_statement'),
            (balance_sheet_url, ticker, 'balance_sheet'),
            (cash_flow_url, ticker, 'cash_flow')
        ]
        financial_tasks.extend(ticker_tasks)
    
    all_tasks = fundamental_tasks + financial_tasks
    print(f"📊 Total tasks to execute: {len(all_tasks)} ({len(fundamental_tasks)} fundamental + {len(financial_tasks)} financial)")
    
    # Execute fundamental data tasks first
    print("🔄 Executing fundamental data persistence tasks...")
    fundamental_results = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(5, len(fundamental_tasks))) as executor:
        # Submit fundamental data tasks
        future_to_task = {
            executor.submit(persist_fundamental_data_worker, task[0]): task 
            for task in fundamental_tasks
        }
        
        for future in concurrent.futures.as_completed(future_to_task):
            task = future_to_task[future]
            ticker_name, task_type = task
            
            try:
                result = future.result()
                fundamental_results.append((ticker_name, task_type, result))
                
                if result:
                    print(f"✅ Successfully completed {ticker_name} {task_type}")
                else:
                    print(f"❌ Failed {ticker_name} {task_type}")
            except Exception as e:
                print(f"❌ Exception occurred during {ticker_name} {task_type}: {e}")
                fundamental_results.append((ticker_name, task_type, False))
    
    print("📊 Step 2: Executing financial data export tasks...")
    
    # Execute financial data tasks
    # Use more workers but limit to reasonable number to avoid overwhelming the server
    max_workers = min(10, len(financial_tasks))  # Max 10 concurrent requests
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit financial data tasks
        future_to_task = {
            executor.submit(export_financial_data_worker, task): task 
            for task in financial_tasks
        }
        
        # Process results as they complete
        financial_results = []
        ticker_results = {}  # Track results per ticker
        
        for future in concurrent.futures.as_completed(future_to_task):
            task = future_to_task[future]
            url, ticker_name, statement_type = task
            
            # Initialize ticker results if not exists
            if ticker_name not in ticker_results:
                ticker_results[ticker_name] = {'success': 0, 'failed': 0, 'total': 3}
            
            try:
                result = future.result()
                financial_results.append((ticker_name, statement_type, result))
                
                if result:
                    print(f"✅ Successfully completed {ticker_name} {statement_type} export")
                    ticker_results[ticker_name]['success'] += 1
                else:
                    print(f"❌ Failed to export {ticker_name} {statement_type}")
                    ticker_results[ticker_name]['failed'] += 1
            except Exception as e:
                print(f"❌ Exception occurred during {ticker_name} {statement_type} export: {e}")
                financial_results.append((ticker_name, statement_type, False))
                ticker_results[ticker_name]['failed'] += 1
    
    # Combine all results for overall summary
    all_results = fundamental_results + financial_results
    total_tasks = len(all_results)
    total_successful = sum(1 for _, _, success in all_results if success)
    total_failed = total_tasks - total_successful
    
    # Fundamental data summary
    fundamental_successful = sum(1 for _, _, success in fundamental_results if success)
    fundamental_failed = len(fundamental_results) - fundamental_successful
    
    print(f"\n📊 Overall Export Summary:")
    print(f"   Total tickers processed: {len(tickers)}")
    print(f"   Total tasks: {total_tasks}")
    print(f"   Total successful: {total_successful}")
    print(f"   Total failed: {total_failed}")
    print(f"   Success rate: {(total_successful/total_tasks*100):.1f}%")
    print(f"\n📋 Task Breakdown:")
    print(f"   Fundamental data: {fundamental_successful}/{len(fundamental_results)} successful")
    print(f"   Financial data: {sum(1 for _, _, success in financial_results if success)}/{len(financial_results)} successful")
    
    # Per-ticker summary
    print(f"\n📋 Per-Ticker Results:")
    fully_successful_tickers = 0
    partially_successful_tickers = 0
    completely_failed_tickers = 0
    
    for ticker, results_info in ticker_results.items():
        success_count = results_info['success']
        failed_count = results_info['failed']
        total_count = results_info['total']
        
        if success_count == total_count:
            status = "🎉 COMPLETE"
            fully_successful_tickers += 1
        elif success_count > 0:
            status = "⚠️  PARTIAL"
            partially_successful_tickers += 1
        else:
            status = "💥 FAILED"
            completely_failed_tickers += 1
        
        print(f"   {ticker}: {status} ({success_count}/{total_count} successful)")
    
    print(f"\n🎯 Final Summary:")
    print(f"   Fully successful tickers: {fully_successful_tickers}")
    print(f"   Partially successful tickers: {partially_successful_tickers}")
    print(f"   Completely failed tickers: {completely_failed_tickers}")
    
    if fully_successful_tickers == len(tickers):
        print(f"🎉🎉🎉 All {len(tickers)} tickers have been successfully exported!")
    elif fully_successful_tickers + partially_successful_tickers > 0:
        print(f"⚠️  Mixed results: {fully_successful_tickers + partially_successful_tickers}/{len(tickers)} tickers had some success")
    else:
        print(f"💥💥💥 All exports failed for all {len(tickers)} tickers")

if __name__ == "__main__":
    main()
