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
            browser = p.chromium.launch(headless=False)

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
        ```json
        {{
            "period_end_quarter": "string",
            "metrics": {{
                "Metric Name 1": numerical_value,
                "Metric Name 2": numerical_value,
                ...
            }}
        }}
        
        Here are the strict instructions for the extraction:

        1. The periods are: {periods}. 
        2. For each period object, create a metrics object.
        3. In the table body, each metric and its values over the periods are in the same row. In the HTML content they are in a div with class "row".
            Identify the metric names in the HTML content. It is in a div element having class name "rowTitle" in each row above.
            Make sure to include all metric names that are present in the HTML content.
        4. For each metric name, find the corresponding values in each period. The metric value is in a div element having "column" class.
        5. Process each numerical value:
            * The final value must be a number without commas. Keep the number as is, but remove any commas.
            * If the value for a specific metric and period is exactly '--', completely omit that metric from the metrics object for that period. Do NOT include metrics with '--' values.
        7. The final output must be only the JSON list. Do not include any introductory or concluding text, explanations, or code block formatting outside of the JSON itself.
        8. Only extract data and periods that are explicitly present in the provided HTML table. Do not infer, assume, or generate any data or periods not directly found in the HTML.

        Here is the HTML content of the table body:
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
