import csv
import os
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

OUTPUT_DIR = "outputs"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def export_financial_data_to_image(url):
  print(f"💲➡️🏞️ Exporting financial data to image...")
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
        
        screenshot_path = os.path.join(OUTPUT_DIR, "income_statement.png")
        page.screenshot(path=screenshot_path, full_page=True)
        browser.close()
  except Exception as e:
      print(f"Error processing URL: {e}")

def save_to_csv(data, filename="financial_data.csv"):
    if data:
        filename = os.path.join(OUTPUT_DIR, filename)
        with open(filename, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            # ... (Write header row)
            writer.writerows(data)

url = "https://finance.yahoo.com/quote/TSLA/financials"

# Export financial data from URL to image
if not os.path.exists(os.path.join(OUTPUT_DIR, "income_statement.png")):
  export_financial_data_to_image(url)
else:
  print("💲💲💲 Income statement image already exists. Moving on...")

model = genai.GenerativeModel(
   model_name="gemini-1.5-pro",
   system_instruction="""
    You are a helpful financial agent that can take in:
    - Input as a screenshot of a financial statement of a company
    - Output as a CSV of the financial data having required columns and rows specified in the prompt
  """
)

# Process the income statement
with open(os.path.join(OUTPUT_DIR, "income_statement.png"), "rb") as img_file:
    image_data = img_file.read()

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
print(f"🏞️➡️📝 Processing income statement image to text output...")
response = model.generate_content([
    {"mime_type": "image/png", "data": image_data},
    income_statement_prompt
])
print(f"✅💾 Done processing income statement image to text output. Now saving to CSV...")

# Convert response to CSV format
if response.text:
    # Use csv.reader to properly parse the text with comma handling
    csv_data = list(csv.reader(response.text.strip().splitlines()))
    
    # Save to CSV
    with open(os.path.join(OUTPUT_DIR, 'financial_data.csv'), 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(csv_data)
    
    print("✅✅✅ Data has been saved to CSV file")
else: 
    print("❌❌❌ No data received from the model")
