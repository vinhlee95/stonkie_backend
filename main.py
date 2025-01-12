import csv
import os
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

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
        
        screenshot_path = "income_statement.png"
        page.screenshot(path=screenshot_path, full_page=True)
        browser.close()
  except Exception as e:
      print(f"Error processing URL: {e}")

def save_to_csv(data, filename="financial_data.csv"):
    if data:
        with open(filename, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            # ... (Write header row)
            writer.writerows(data)

url = "https://finance.yahoo.com/quote/TSLA/financials"

# Export financial data from URL to image
export_financial_data_to_image(url)

model = genai.GenerativeModel(
   model_name="gemini-1.5-pro",
   system_instruction="""
    You are a helpful financial agent that can take in:
    - Input as a screenshot of a financial statement of a company
    - Output as a CSV of the financial data having:
      - Years as columns
      - Metrics as rows:
        - Total Revenue
        - Gross Profit
        - Operating Income
        - Operating Expenses
        - Diluted EPS
        - Net Income
        - Gross Profit Margin in percentage (gross profit / total revenue)
        - Operating Profit Margin in percentage (operating income / total revenue)
        - Net Profit Margin in percentage (net income / total revenue)
  """
)

# Load the image natively
with open("income_statement.png", "rb") as img_file:
    image_data = img_file.read()

response = model.generate_content([
    {"mime_type": "image/png", "data": image_data},
    "Please analyze this financial statement and provide the data in the specified format."
])

# Convert response to CSV format
if response.text:
    # Split the response into lines and process
    lines = response.text.strip().split('\n')
    csv_data = [line.split(',') for line in lines]
    
    # Save to CSV
    with open('financial_data.csv', 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerows(csv_data)
    
    print("✅✅✅ Data has been saved to financial_data.csv")
else: 
    print("❌❌❌ No data received from the model")
