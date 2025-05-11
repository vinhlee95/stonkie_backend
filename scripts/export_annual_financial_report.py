import os
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import google.generativeai as genai
import json
from PIL import Image

from connectors.database import get_db
from models.company_financial_statement import CompanyFinancialStatement

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

OUTPUT_DIR = "outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def export_financial_data_to_image(url, file_name):
    print(f"💲➡️🏞️ Exporting financial data to {file_name} image...")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={'width': 1920, 'height': 1080})
            page.goto(url)

            page.wait_for_selector('.accept-all')
            page.click('.accept-all')

            page.wait_for_timeout(1500)

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

def get_prompt_from_ocr_text(ocr_text):
  return f"""
    You are an expert at converting financial tables from text to JSON format. You will receive text extracted from an image of a financial table. Your task is to output the data in a JSON format of a list. Each item in the list is an object with the following keys:
    - period_end_year: number
    - metrics: object
      - metric_name: value of the metric in given period

    Here's the extracted text:
    {ocr_text}

    Output ONLY the JSON object that matches the structure above. Do not include any other text.
  """

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

def export_financial_data_to_db(url, ticker, statement_type):
    print(f"💲🗂️ Exporting financial data for {ticker} {statement_type} to database...")
    
    # Export the financial data to an image
    file_name = f"{ticker.lower()}_{statement_type}"
    image_path = os.path.join(OUTPUT_DIR, f"{file_name}.png")
    export_financial_data_to_image(url, file_name)
    
    print("✅📘 Done capturing the image. Now feeding it to the model...")
    
    # Create the model with image processing capability
    model = genai.GenerativeModel(
        model_name="gemini-2.5-pro-preview-03-25",
    )
    
    # Create the prompt
    prompt = """
    You are an expert at converting financial tables from images to JSON format. Your task is to output the data in a JSON format of a list. Each item in the list is an object with the following keys:
    - period_end_year: number (extract from the date, e.g., 2024 from "12/31/2024"). If the date is TTM, put 'TTM' as period_end_year.
    - metrics: object
      - metric_name: value of the metric in given period (all numbers should be in thousands, remove commas)

    The output should be a valid JSON array. Do not include any explanatory text or markdown formatting.
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
    ticker = input("Enter stock ticker symbol (e.g., TSLA, AAPL): ").strip()
    
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
