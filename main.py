import csv
import os
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import google.generativeai as genai
from google.cloud import vision
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
from typing import Dict

def parse_text_from_image(path):
    print(f"🔍🔍 Parsing text from image: {path}")
    client = vision.ImageAnnotatorClient()
    with open(path, "rb") as image_file:
        content = image_file.read()

    image = vision.Image(content=content)
    # Use regular text detection for tables/financial data
    response = client.text_detection(image=image)
    
    # Check for errors
    if response.error.message:
        raise Exception(
            '{}\nFor more info on error messages, check: '
            'https://cloud.google.com/apis/design/errors'.format(
                response.error.message))
    
    # Get the text from the response
    if response.text_annotations:
        doc_text = response.text_annotations[0].description
    else:
        return ""

    # Basic Cleaning (Expand as needed)
    lines = doc_text.splitlines()
    cleaned_lines = [line.strip() for line in lines if line.strip()]
    cleaned_text = "\n".join(cleaned_lines)

    return cleaned_text

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

        # Cookie acceptance
        # page.wait_for_selector('#scroll-down-btn')
        # page.click('#scroll-down-btn')

        page.wait_for_selector('.accept-all')
        page.click('.accept-all')

        page.wait_for_timeout(1500)

        # Expand all metrics
        page.wait_for_selector('span.expand')
        page.click('span.expand')

        page.screenshot(path=os.path.join(OUTPUT_DIR, f"{file_name}.png"), full_page=True)
        browser.close()
  except Exception as e:
      print(f"Error processing URL: {e}")

def save_to_csv(data, filename="financial_data.csv", output_dir="outputs"):
    """
    Save data to a CSV file in the specified output directory.
    
    Args:
        data: Either a string containing CSV data or a list of rows
        filename (str): Name of the output CSV file
        output_dir (str): Directory to save the CSV file
    """
    try:
        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)
        
        # Create full file path
        filepath = os.path.join(output_dir, filename)
        
        # Convert string data to list if needed
        if isinstance(data, str):
            data = list(csv.reader(data.strip().splitlines()))
        
        # Write to CSV file
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerows(data)
            
        print(f"✅✅✅ Data has been saved to {filepath}")
        
    except Exception as e:
        print(f"❌❌❌ Error saving CSV file: {e}")


model = genai.GenerativeModel(
   model_name="gemini-1.5-pro",
   system_instruction="""
    You are a helpful financial agent that can take in:
    - Input as a screenshot of a financial statement of a company
    - Output as a CSV of the financial data having required columns and rows specified in the prompt
  """
)

def get_prompt_from_ocr_text(ocr_text):
  return f"""
    You are an expert at converting financial tables from text to CSV format. You will receive text extracted from an image of a financial table. Your task is to output the data in a comma-separated value (CSV) format.

    Here are the key aspects of the table's structure:

    *   The first column contains the "Breakdown" or description of the financial metric.
    *   The subsequent columns represent time periods (TTM, 12/31/2023, 12/31/2022, etc.).
    *   All numbers are in thousands. Do not keep the commas in the numbers.

    Here's the extracted text:
    {ocr_text}

    Output the data as CSV, including a header row. Do not include any explanatory text or comments in the CSV output.
  """

"""
Main function that takes in the URL of the financial statement and the file name:
- Export the financial data to an image
- Process the image to a CSV
"""
def export_financial_data_to_csv(url, file_name, force=False):
  # Check if the output already exists
  if os.path.exists(os.path.join(OUTPUT_DIR, f"{file_name}.csv")) and not force:
    print(f"✅💲 {file_name} CSV already exists in {OUTPUT_DIR}. Enjoy investing!")
    return

  if not os.path.exists(os.path.join(OUTPUT_DIR, f"{file_name}.png")):
    export_financial_data_to_image(url, file_name)
  else:
    print(f"✅🏞️ {file_name} image already exists in {OUTPUT_DIR}.")

  # Use Google Vision to extract the text from the image
  ocr_text = parse_text_from_image(os.path.join(OUTPUT_DIR, f"{file_name}.png"))
  print("✅📘 Done parsing the text from the image. Now feeding it to the model...")

  response = model.generate_content(get_prompt_from_ocr_text(ocr_text))

  # Convert response to CSV format
  if response.text:
      save_to_csv(response.text, f'{file_name}.csv', OUTPUT_DIR)
  else:
      print("❌❌❌ No data received from the model")

def get_financial_urls(ticker):
    """
    Generate Yahoo Finance URLs for a given ticker symbol
    
    Args:
        ticker (str): Stock ticker symbol (e.g., 'TSLA', 'AAPL')
    
    Returns:
        tuple: URLs for financial statement, balance sheet and cash flow
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
    export_financial_data_to_csv(
        financial_statement_url, 
        f"{ticker.lower()}_income_statement", 
    )

    export_financial_data_to_csv(
        balance_sheet_url, 
        f"{ticker.lower()}_balance_sheet", 
    )

    export_financial_data_to_csv(
        cash_flow_url, 
        f"{ticker.lower()}_cash_flow", 
    )

app = FastAPI()

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Add your React app's URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/financial-data/{ticker}/{report_type}")
async def get_financial_data(ticker: str, report_type: str) -> Dict:
    """
    Get financial data for a specific ticker and report type
    report_type can be: income_statement, balance_sheet, or cash_flow
    """
    try:
        file_path = os.path.join(OUTPUT_DIR, f"{ticker.lower()}_{report_type}.csv")
        if not os.path.exists(file_path):
            # Generate the file if it doesn't exist
            urls = get_financial_urls(ticker)
            url_map = {
                "income_statement": urls[0],
                "balance_sheet": urls[1],
                "cash_flow": urls[2]
            }
            export_financial_data_to_csv(url_map[report_type], f"{ticker.lower()}_{report_type}")
        
        print(f"Already exported {ticker.lower()}_{report_type}.csv")
        # Read CSV and convert to JSON
        df = pd.read_csv(file_path)
        print(df)
        print(df.columns.tolist())
        print(df.to_dict('records'))
        
        return {
            "data": df.to_dict('records'),
            "columns": df.columns.tolist()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
