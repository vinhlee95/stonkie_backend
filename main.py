import csv
import os
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

OUTPUT_DIR = "outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def export_financial_data_to_image(url, file_name):
  print(f"💲➡️🏞️ Exporting financial data to {file_name} image...")
  try:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url)

        # Cookie acceptance
        page.wait_for_selector('#scroll-down-btn')
        page.click('#scroll-down-btn')

        page.wait_for_selector('.accept-all')
        page.click('.accept-all')

        page.wait_for_timeout(2000)

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

income_statement_prompt = """
    The output should be in a CSV format with the following columns and rows:
    - Years as columns
    - Metrics as rows:
      - Total Revenue
      - Gross Profit
      - Operating Income
      - Operating Expenses
      - Diluted EPS
      - Net Income
      - EBIT
      - EBITDA
      - Gross Profit Margin in percentage (gross profit / total revenue)
      - Operating Profit Margin in percentage (operating income / total revenue)
      - Net Profit Margin in percentage (net income / total revenue)
  """

balance_sheet_prompt = """
    The output should be in a CSV format with the following columns and rows:
    - Years as columns
    - Metrics as rows:
      - Total Assets
      - Current Assets
      - Inventory
      - Cash and Cash Equivalents
      - Total Liabilities
      - Current Liabilities
      - Common Stock Equity
      - Retained Earnings

      - Liquidity Ratio (Current Assets / Current Liabilities)
      - Debt Ratio (Total Liabilities / Total Assets)
"""

"""
Main function that takes in the URL of the financial statement and the file name:
- Export the financial data to an image
- Process the image to a CSV
"""
def export_financial_data_to_csv(url, file_name, prompt):
  # Check if the output already exists
  if os.path.exists(os.path.join(OUTPUT_DIR, f"{file_name}.csv")):
    print(f"💲💲💲 {file_name} CSV already exists in {OUTPUT_DIR}.")
    return

  export_financial_data_to_image(url, file_name)

  # Process the financial data in the image file
  with open(os.path.join(OUTPUT_DIR, f"{file_name}.png"), "rb") as img_file:
    image_data = img_file.read()

  print(f"🏞️➡️📝 Processing income statement image to text output...")
  response = model.generate_content([
      {"mime_type": "image/png", "data": image_data},
      prompt
  ])
  print(f"✅💾 Done processing the {file_name} image to text output. Now saving to CSV...")

  # Convert response to CSV format
  if response.text:
      save_to_csv(response.text, f'{file_name}.csv', OUTPUT_DIR)
  else:
      print("❌❌❌ No data received from the model")


TSLA_FINANCIAL_STATEMENT_URL = "https://finance.yahoo.com/quote/TSLA/financials"
TSLA_BALANCE_SHEET_URL = "https://finance.yahoo.com/quote/TSLA/balance-sheet"

export_financial_data_to_csv(TSLA_FINANCIAL_STATEMENT_URL, "income_statement", income_statement_prompt)
export_financial_data_to_csv(TSLA_BALANCE_SHEET_URL, "balance_sheet", balance_sheet_prompt)