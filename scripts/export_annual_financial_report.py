from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import re

from connectors.database import get_db
from models.company_financial_statement import CompanyFinancialStatement
from agent.agent import Agent

load_dotenv()

def save_to_database(ticker, statement_type, data):
    """
    Save financial data to the database
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
            
            # Check if record exists
            existing_record = db.query(CompanyFinancialStatement).filter(
                CompanyFinancialStatement.company_symbol == ticker.upper(),
                CompanyFinancialStatement.period_end_year == period_end_year,
            ).first()
            
            if existing_record:
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
                # Create new record
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
        
        # Commit all changes
        db.commit()
        print(f"✅✅✅ Financial data for {ticker} {statement_type} has been saved to the database")
        
    except Exception as e:
        print(f"❌❌❌ Failed to save financial data to the database: {e}")
        return

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
            page.goto(url)

            page.wait_for_selector('.accept-all')
            page.click('.accept-all')
            page.wait_for_timeout(2500)

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

def export_financial_data_to_db(url, ticker, statement_type):
    print(f"💲🗂️ Exporting annual financial data for {ticker} {statement_type} to database...")
    
    result = export_financial_data_to_text(url)
    if not result:
        print("❌❌❌ Failed to extract financial data from URL")
        return
        
    table_header_html, table_body_html = result

    periods = re.findall(r'>([^<]+)<\/div>', table_header_html)
    periods = [p.strip() for p in periods]
    periods = [p for p in periods if p != 'Breakdown' and p != '']
    print(f"✅📘 Extracted periods: {periods}")
    print("✅📘 Done getting HTML content of the financial statement. Now feeding it to the model...")
    
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
        print(json_response)
        save_to_database(ticker, statement_type, json_response)
    else:
        print("❌❌❌ No data received from the model")

def get_financial_urls(ticker):
    """
    Generate Yahoo Finance URLs for a given ticker symbol
    """
    base_url = f"https://finance.yahoo.com/quote/{ticker.upper()}"
    return (
        f"{base_url}/financials",
        f"{base_url}/balance-sheet",
        f"{base_url}/cash-flow"
    )

def main():
    # Get ticker symbol from user
    ticker = input("Enter stock ticker symbol (e.g., TSLA, AAPL): ").strip().upper()
    
    # Generate URLs for the given ticker
    financial_statement_url, balance_sheet_url, cash_flow_url = get_financial_urls(ticker)
    
    export_financial_data_to_db(
        financial_statement_url, 
        ticker,
        'income_statement'
    )

    export_financial_data_to_db(
        balance_sheet_url, 
        ticker,
        'balance_sheet'
    )

    export_financial_data_to_db(
        cash_flow_url, 
        ticker,
        'cash_flow'
    )

if __name__ == "__main__":
    main()
