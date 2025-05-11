import os
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import google.generativeai as genai
import json
from PIL import Image

from connectors.database import get_db
from models.company_quarterly_financial_statement import CompanyQuarterlyFinancialStatement

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

OUTPUT_DIR = "outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def export_financial_data_to_image(url, file_name):
    print(f"💲➡️🏞️ Exporting financial data to {file_name} image...")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            page = browser.new_page(viewport={'width': 1920, 'height': 1080})
            page.goto(url)

            page.wait_for_selector('.accept-all')
            page.click('.accept-all')
            page.wait_for_timeout(1500)

            # Click the quarterly tab
            page.wait_for_selector('button#tab-quarterly')
            page.click('button#tab-quarterly')
            page.wait_for_timeout(2500)  # Wait for data to load

            page.wait_for_selector('span.expand')
            page.click('span.expand')

            page.screenshot(path=os.path.join(OUTPUT_DIR, f"{file_name}.png"), full_page=True)
            browser.close()
    except Exception as e:
        print(f"Error processing URL: {e}")

def is_number(s):
    try:
      float(s)
      return True
    except ValueError:
      return False

def parse_financial_data(text_data):
    """
    Parse the financial data from JSON text into a structured format
    Returns a dictionary with the financial data
    """
    try:
        return json.loads(text_data)
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse JSON data: {e}")
        return None

model = genai.GenerativeModel(
  #  model_name="gemini-1.5-pro"
  model_name="gemini-2.5-pro-preview-03-25"
)

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
    
    # Export the financial data to an image
    file_name = f"{ticker.lower()}_{statement_type}_quarterly"
    image_path = os.path.join(OUTPUT_DIR, f"{file_name}.png")
    export_financial_data_to_image(url, file_name)
    
    print("✅📘 Done capturing the image. Now feeding it to the model...")
    
    # Create the model with image processing capability
    model = genai.GenerativeModel(
        model_name="gemini-2.5-pro-preview-03-25",
    )
    
    # Create the prompt
    prompt = """
        You are an expert at converting financial tables from images into a structured JSON format. Your primary goal is to accurately extract financial metrics and their corresponding values for specific periods presented as columns in the table image.
        Follow these steps precisely:
        1. Identify Valid Periods: Scan the top row(s) of the table to identify the exact column headers representing the financial periods (e.g., "12/31/2023", "09/30/2023", etc.). Create an internal list of only these valid period_end_quarter strings. These are the ONLY periods you should include in the final output.
        2. Process Rows: Go through each row of the table that contains financial metric data.
        3. Identify Metric Name: For each row, identify the name of the financial metric (e.g., "Total Revenue", "Net Income").
        4. Extract and Align Data: For the current metric row, iterate through the columns you identified in step 1. For each valid period_end_quarter column:
            - Find the value in the same row that aligns vertically with that specific period_end_quarter header.
            - Ensure the value found truly corresponds to the identified metric for that specific period's column.
            - Convert the numerical value: remove any commas and assume the value is in thousands (as per your original instruction).
            - Associate this processed value with the current metric name and the exact period_end_quarter string from step 1.
        5. Construct JSON: Build the final JSON output as a list of objects. Each object in the list represents a single period and must have the following structure:
            - period_end_quarter: string. This MUST be one of the exact valid period strings identified in step 1.
            - metrics: object. This object contains key-value pairs where the Key is the metric name (string) and the Value is the processed numerical value (number, without commas, representing thousands) extracted for that metric in that specific period_end_quarter column.
        
        Constraints and Exclusions:
            - The output must be a valid JSON array.
            - Do not include any introductory or explanatory text outside the JSON.
            - Do not include any markdown formatting (like ```json).
            - CRITICALLY IMPORTANT: Do not include data for any period not explicitly present as a column header in the image. Absolutely do not hallucinate or infer data for periods like "12/31/2022" if it's not a column header.
            - Do not include data from "TTM" or "TTM (Trailing Twelve Months)" columns. Identify and ignore these columns.
            - Ensure the numerical values are correctly associated with the exact column header they appear under in the image. Double-check the vertical alignment.
    """
    
    # Generate content with the image
    response = model.generate_content([prompt, Image.open(image_path)])
    
    # Convert response to structured data and save to database
    if response.text:
        # Try to extract JSON from the response if it's wrapped in markdown
        json_text = response.text.strip()
        if json_text.startswith('```json'):
            json_text = json_text[7:]
        if json_text.startswith('```'):
            json_text = json_text[3:]
        if json_text.endswith('```'):
            json_text = json_text[:-3]
        json_text = json_text.strip()
        
        print("📊 Cleaned JSON text:")
        print(json_text)
        
        data = parse_financial_data(json_text)
        if data:
            save_to_database(ticker, statement_type, data)
        else:
            print("❌❌❌ Failed to parse financial data")
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
    
    # Process financial data
    export_financial_data_to_db(
        financial_statement_url, 
        ticker,
        "income_statement"
    )

    export_financial_data_to_db(
        balance_sheet_url, 
        ticker,
        "balance_sheet"
    )

    export_financial_data_to_db(
        cash_flow_url, 
        ticker,
        "cash_flow"
    )

if __name__ == "__main__":
    main()
