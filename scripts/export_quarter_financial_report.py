from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import re

from agent.agent import Agent
from connectors.database import get_db
from models.company_quarterly_financial_statement import CompanyQuarterlyFinancialStatement

load_dotenv()

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

            # Click the quarterly tab
            tab_quarterly_button = page.locator('button#tab-quarterly')
            tab_quarterly_button.wait_for(state="visible")
            tab_quarterly_button.scroll_into_view_if_needed()
            tab_quarterly_button.click(force=True)
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

def save_to_database(ticker, statement_type, data):
    """
    Save financial data to the database
    """
    try:
        db = next(get_db())
        
        # Process each item
        for item in data:
            period_end_quarter = item['period_end_quarter']
            
            # Check if record exists
            existing_record = db.query(CompanyQuarterlyFinancialStatement).filter(
                CompanyQuarterlyFinancialStatement.company_symbol == ticker.upper(),
                CompanyQuarterlyFinancialStatement.period_end_quarter == period_end_quarter,
            ).first()
            
            if existing_record:
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
                # Create new record
                record = CompanyQuarterlyFinancialStatement(
                    company_symbol=ticker.upper(),
                    period_end_quarter=period_end_quarter,
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

def export_financial_data_to_db(url, ticker, statement_type):
    print(f"💲🗂️ Exporting quarterly financial data for {ticker} {statement_type} to database...")
    
    table_header_html, table_body_html = export_financial_data_to_text(url)

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
        print(json_response)
        # Filter out "TTM" period if present
        data_to_save = [item for item in json_response if item["period_end_quarter"] != "TTM"]

        save_to_database(ticker, statement_type, data_to_save)
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
