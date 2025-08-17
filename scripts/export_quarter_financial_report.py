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
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)

            # Create a fresh incognito-like context
            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                java_script_enabled=True,
                user_agent="Mozilla/5.0",  # Optional: customize user agent
                ignore_https_errors=True,  # Optional: for sites with cert issues
                locale='en-US',  # Optional: set preferred language
                storage_state=None  # ensures no session storage/cookies
            )
            page = context.new_page()
            page.goto(url, timeout=15000)  # 15 second timeout

            page.wait_for_selector('.accept-all')
            page.click('.accept-all')
            page.wait_for_timeout(2500)

            # Click the quarterly tab
            tab_quarterly_button = page.locator('button#tab-quarterly')
            tab_quarterly_button.wait_for(state="visible")
            tab_quarterly_button.scroll_into_view_if_needed()
            tab_quarterly_button.click(force=True)
            page.wait_for_timeout(5000)

            expand_button = page.locator('span.expand')
            expand_button.wait_for(state="visible")
            expand_button.click()
            page.wait_for_timeout(2500)

            table_header = page.locator('div[class*="tableHeader"]')
            table_body = page.locator('div[class*="tableBody"]')

            header_html = table_header.inner_html()
            body_html = table_body.inner_html()

            browser.close()
            return (header_html, body_html)
    except Exception as e:
        print(f"Error processing URL: {e}")

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
            
        table_header_html, table_body_html = result

        periods = re.findall(r'>([^<]+)<\/div>', table_header_html)
        periods = [p.strip() for p in periods]
        periods = [p for p in periods if p != 'Breakdown' and p != '']
        print(f"✅📘 Extracted periods: {periods}")
        
        # Validate that periods are quarterly (3 months apart)
        is_valid, error_message = validate_quarterly_periods(periods)
        if not is_valid:
            print(f"❌❌❌ Skipping {ticker} {statement_type} - Invalid quarterly periods: {error_message}")
            print(f"❌📘 Raw header HTML: {table_header_html}")
            return False
        
        print(f"✅📘 Periods validation passed for {statement_type}: {error_message}")
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
    # Get ticker symbol from user
    ticker = input("Enter stock ticker symbol (e.g., TSLA, AAPL): ").strip().upper()
    
    # Generate URLs for the given ticker
    financial_statement_url, balance_sheet_url, cash_flow_url = get_financial_urls(ticker)
    
    # Prepare tasks for parallel execution
    tasks = [
        (financial_statement_url, ticker, 'income_statement'),
        (balance_sheet_url, ticker, 'balance_sheet'),
        (cash_flow_url, ticker, 'cash_flow')
    ]
    
    print(f"🚀 Starting parallel export of financial data for {ticker}...")
    
    # Execute tasks in parallel using ThreadPoolExecutor
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        # Submit all tasks
        future_to_task = {
            executor.submit(export_financial_data_worker, task): task 
            for task in tasks
        }
        
        # Process results as they complete
        results = []
        for future in concurrent.futures.as_completed(future_to_task):
            task = future_to_task[future]
            url, ticker_name, statement_type = task
            
            try:
                result = future.result()
                results.append((statement_type, result))
                if result:
                    print(f"✅ Successfully completed {statement_type} export")
                else:
                    print(f"❌ Failed to export {statement_type}")
            except Exception as e:
                print(f"❌ Exception occurred during {statement_type} export: {e}")
                results.append((statement_type, False))
    
    # Summary
    successful = sum(1 for _, success in results if success)
    total = len(results)
    
    print(f"\n📊 Export Summary:")
    print(f"   Total tasks: {total}")
    print(f"   Successful: {successful}")
    print(f"   Failed: {total - successful}")
    
    if successful == total:
        print(f"🎉 All financial data for {ticker} has been successfully exported!")
    elif successful > 0:
        print(f"⚠️  Partial success: {successful}/{total} exports completed")
    else:
        print(f"💥 All exports failed for {ticker}")

if __name__ == "__main__":
    main()
