from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import re
import concurrent.futures
from datetime import datetime
from dateutil.relativedelta import relativedelta
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from agent.agent import Agent
from connectors.database import engine
from models.company_quarterly_financial_statement import CompanyQuarterlyFinancialStatement
from services.company import CompanyConnector

load_dotenv()

def validate_quarterly_periods(periods):
    """
    Validate that periods are quarterly (3 months apart) and properly formatted.
    Returns (is_valid, error_message)
    """
    try:
        # Filter out TTM and empty periods
        quarterly_periods = [p for p in periods if p != 'TTM' and p.strip() != '']
        
        if len(quarterly_periods) < 2:
            return False, f"Not enough periods to validate (found {len(quarterly_periods)}, need at least 2)"
        
        # Parse dates
        parsed_dates = []
        for period in quarterly_periods:
            try:
                # Try different date formats
                date_formats = ['%m/%d/%Y', '%m/%d/%y', '%Y-%m-%d']
                parsed_date = None
                
                for fmt in date_formats:
                    try:
                        parsed_date = datetime.strptime(period.strip(), fmt)
                        break
                    except ValueError:
                        continue
                
                if parsed_date is None:
                    return False, f"Could not parse date format for period: {period}"
                
                parsed_dates.append((period, parsed_date))
            except Exception as e:
                return False, f"Error parsing period '{period}': {str(e)}"
        
        # Sort dates in descending order (most recent first)
        parsed_dates.sort(key=lambda x: x[1], reverse=True)
        
        # Check if consecutive periods are 3 months apart (allowing for some tolerance)
        for i in range(len(parsed_dates) - 1):
            current_period, current_date = parsed_dates[i]
            next_period, next_date = parsed_dates[i + 1]
            
            # Calculate the difference
            expected_previous_date = current_date - relativedelta(months=3)
            
            # Allow for some tolerance (up to 5 days difference)
            date_diff = abs((next_date - expected_previous_date).days)
            
            if date_diff > 5:
                return False, f"Periods are not quarterly: {current_period} and {next_period} are not 3 months apart (difference: {date_diff} days from expected)"
        
        return True, "All periods are valid quarterly intervals"
        
    except Exception as e:
        return False, f"Unexpected error during validation: {str(e)}"

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

                # Strategy 5: Wait for the financial data table structure to be present
                print("⏳ Waiting for financial table structure...")
                try:
                    # Wait for either the table or a loading indicator
                    page.wait_for_function("""
                        () => {
                            // Check if table structure exists
                            const tableHeader = document.querySelector('div[class*="tableHeader"]');
                            const tableBody = document.querySelector('div[class*="tableBody"]');
                            const tabs = document.querySelector('button#tab-quarterly');
                            
                            return tableHeader && tableBody && tabs;
                        }
                    """, timeout=10000)
                    print("✅ Financial table structure is ready")
                except:
                    print("⚠️  Financial table structure not fully loaded, proceeding anyway...")

                # Now proceed with quarterly tab clicking
                print("🔄 Starting quarterly tab selection...")
                tab_quarterly_button = page.locator('button#tab-quarterly')
                tab_quarterly_button.wait_for(state="visible", timeout=10000)
                tab_quarterly_button.scroll_into_view_if_needed()
                
                # Check if already selected
                aria_selected = tab_quarterly_button.get_attribute('aria-selected')
                if aria_selected == 'true':
                    print("✅ Quarterly tab is already selected")
                    quarterly_selected = True
                else:
                    print("🔄 Clicking quarterly tab...")
                    tab_quarterly_button.click(force=True)
                    
                    # Wait and verify the click worked by checking aria-selected
                    page.wait_for_timeout(3000)  # Increased wait time
                    
                    # Verify the click was successful
                    max_verification_attempts = 3
                    quarterly_selected = False
                    
                    for attempt in range(max_verification_attempts):
                        aria_selected_after = tab_quarterly_button.get_attribute('aria-selected')
                        if aria_selected_after == 'true':
                            print(f"✅ Quarterly tab successfully selected (aria-selected=true) after {attempt + 1} attempts")
                            quarterly_selected = True
                            break
                        else:
                            print(f"⚠️  Attempt {attempt + 1}: Quarterly tab not selected (aria-selected={aria_selected_after})")
                            if attempt < max_verification_attempts - 1:
                                # Try different click methods
                                if attempt == 0:
                                    # Try JavaScript click
                                    page.evaluate('document.querySelector("#tab-quarterly").click()')
                                    print("🔄 Tried JavaScript click")
                                elif attempt == 1:
                                    # Try dispatch event
                                    tab_quarterly_button.dispatch_event('click')
                                    print("🔄 Tried dispatch event click")
                                
                                page.wait_for_timeout(3000)  # Longer wait between attempts
                
                # If quarterly tab selection failed, try browser restart
                if not quarterly_selected:
                    print(f"❌ Failed to select quarterly tab on browser attempt {browser_attempt + 1}")
                    browser.close()
                    
                    if browser_attempt < max_browser_restarts:
                        print(f"🔄 Restarting browser and retrying... (attempt {browser_attempt + 2}/{max_browser_restarts + 1})")
                        continue  # Restart browser
                    else:
                        print("❌❌❌ Failed to select quarterly tab after all browser restart attempts")
                        return None
                
                # Wait for content to update after tab selection
                print("⏳ Waiting for quarterly content to load...")
                page.wait_for_timeout(5000)  # Increased wait time for content refresh
                
                try:
                    # Wait for table content to be refreshed after tab click
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

                # Validate quarterly periods before returning
                print("🔍 Validating extracted periods...")
                periods = re.findall(r'>([^<]+)<\/div>', header_html)
                periods = [p.strip() for p in periods]
                periods = [p for p in periods if p != 'Breakdown' and p != '']
                print(f"✅📘 Extracted periods: {periods}")
                
                # Validate that periods are quarterly (3 months apart)
                is_valid, error_message = validate_quarterly_periods(periods)
                if not is_valid:
                    print(f"❌ Quarterly periods validation failed on browser attempt {browser_attempt + 1}: {error_message}")
                    print(f"❌📘 Raw header HTML: {header_html[:200]}...")
                    browser.close()
                    
                    if browser_attempt < max_browser_restarts:
                        print(f"🔄 Restarting browser due to validation failure... (attempt {browser_attempt + 2}/{max_browser_restarts + 1})")
                        continue  # Restart browser
                    else:
                        print("❌❌❌ Failed quarterly validation after all browser restart attempts")
                        return None

                print(f"✅📘 Periods validation passed. Using periods: {periods} for {url}")

                browser.close()
                print(f"✅ Successfully extracted and validated data on browser attempt {browser_attempt + 1}")
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

def save_to_database(ticker, statement_type, data):
    """
    Save financial data to the database with concurrency safety
    """
    # Create a new session for thread safety
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    
    try:
        # Process each item
        for item in data:
            period_end_quarter = item['period_end_quarter']
            
            # Use atomic upsert with retry logic for better concurrency safety
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    # Try to get existing record with SELECT FOR UPDATE to lock it
                    existing_record = db.query(CompanyQuarterlyFinancialStatement).filter(
                        CompanyQuarterlyFinancialStatement.company_symbol == ticker.upper(),
                        CompanyQuarterlyFinancialStatement.period_end_quarter == period_end_quarter,
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
                            print(f"🔄 Skipping existing record for {ticker} {statement_type} {period_end_quarter} because {statement_type} is already populated.")
                            break

                        print(f"🔄 Updating existing record for {ticker} {statement_type} {period_end_quarter}")
                        # Update existing record
                        if statement_type == 'income_statement':
                            existing_record.income_statement = item['metrics']
                        elif statement_type == 'balance_sheet':
                            existing_record.balance_sheet = item['metrics']
                        elif statement_type == 'cash_flow':
                            existing_record.cash_flow = item['metrics']
                    else:
                        print(f"🔄 Creating new record for {ticker} {statement_type} {period_end_quarter}")
                        # Create new record with only the current statement type
                        record = CompanyQuarterlyFinancialStatement(
                            company_symbol=ticker.upper(),
                            period_end_quarter=period_end_quarter,
                        )
                        
                        # Set only the current statement type
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
                    
                # TODO: this is actually never raised due to no constraint in DB level. Hence in race condition, 2 different processes will just create 2 different (duplicated) rows
                # for the ticker in the same period
                # - Add DB constraint by ticker + period probably so that this exception is valid
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

def export_financial_data_to_db(url, ticker, statement_type):
    """
    Export financial data from a URL to the database
    Returns True if successful, False otherwise
    """
    try:
        print(f"💲🗂️ Exporting quarterly financial data for {ticker} {statement_type} to database...")
        
        result = export_financial_data_to_text(url)
        if result is None:
            print(f"❌ Failed to extract data from {url}")
            return False
            
        table_body_html, periods = result

        print(f"✅📘 Done getting HTML content of the {statement_type}. Now feeding it to the model...")
        
        openai_agent = Agent(model_type="openai")

        final_prompt = f"""
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
            - Even if a metric has -- for all periods, still include its name and row position when processing. It may have valid data in the future or in other contexts.
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

        json_response = openai_agent.generate_content(prompt=final_prompt, stream=False)
        if json_response:
            print("✅✅✅ Successfully extracted financial data from the model to JSON")
            # Filter out "TTM" period if present
            data_to_save = [item for item in json_response if item["period_end_quarter"] != "TTM"]

            save_to_database(ticker, statement_type, data_to_save)
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
    # Get all ticker symbols from database
    company_fundamental_connector = CompanyConnector()
    tickers = company_fundamental_connector.get_all_company_tickers()

    if not tickers:
        print("❌ No tickers found in database")
        return
    
    print(f"🚀 Starting parallel export for {len(tickers)} tickers: {tickers}")
    
    # Prepare tasks for all tickers - each ticker has 3 statement types
    all_tasks = []
    for ticker in tickers:
        financial_statement_url, balance_sheet_url, cash_flow_url = get_financial_urls(ticker)
        ticker_tasks = [
            (financial_statement_url, ticker, 'income_statement'),
            (balance_sheet_url, ticker, 'balance_sheet'),
            (cash_flow_url, ticker, 'cash_flow')
        ]
        all_tasks.extend(ticker_tasks)
    
    print(f"� Total tasks to execute: {len(all_tasks)}")
    
    # Execute all tasks in parallel with increased worker pool
    # Use more workers but limit to reasonable number to avoid overwhelming the server
    max_workers = min(10, len(all_tasks))  # Max 10 concurrent requests
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_task = {
            executor.submit(export_financial_data_worker, task): task 
            for task in all_tasks
        }
        
        # Process results as they complete
        results = []
        ticker_results = {}  # Track results per ticker
        
        for future in concurrent.futures.as_completed(future_to_task):
            task = future_to_task[future]
            url, ticker_name, statement_type = task
            
            # Initialize ticker results if not exists
            if ticker_name not in ticker_results:
                ticker_results[ticker_name] = {'success': 0, 'failed': 0, 'total': 3}
            
            try:
                result = future.result()
                results.append((ticker_name, statement_type, result))
                
                if result:
                    print(f"✅ Successfully completed {ticker_name} {statement_type} export")
                    ticker_results[ticker_name]['success'] += 1
                else:
                    print(f"❌ Failed to export {ticker_name} {statement_type}")
                    ticker_results[ticker_name]['failed'] += 1
            except Exception as e:
                print(f"❌ Exception occurred during {ticker_name} {statement_type} export: {e}")
                results.append((ticker_name, statement_type, False))
                ticker_results[ticker_name]['failed'] += 1
    
    # Overall summary
    total_tasks = len(results)
    total_successful = sum(1 for _, _, success in results if success)
    total_failed = total_tasks - total_successful
    
    print(f"\n📊 Overall Export Summary:")
    print(f"   Total tickers processed: {len(tickers)}")
    print(f"   Total tasks: {total_tasks}")
    print(f"   Total successful: {total_successful}")
    print(f"   Total failed: {total_failed}")
    print(f"   Success rate: {(total_successful/total_tasks*100):.1f}%")
    
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
