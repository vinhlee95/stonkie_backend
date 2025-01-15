import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

# Configure Gemini
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

model = genai.GenerativeModel(
    model_name="gemini-1.5-pro",
    system_instruction="""
    You are a professional financial analyst who specializes in analyzing company financial statements.
    Provide clear, concise analysis focusing on:
    1. Key financial metrics and their trends
    2. Company's financial health
    3. Areas of strength and concern
    4. Year-over-year growth rates in these metrics, sorted by year in descending order:
      - Total assets
      - Total liabilities
      - Total equity
      - Stockholders' equity
      - Gross Profit
      - Net Income
      - Profit Margin
      - Operating Cash Flow
      - Free Cash Flow
    5. Recommendations for investors

    Only provide the analysis from the source data given in the prompt.
    Do not make up any information or share information that is not provided in the source data.
    """
)

analysis_prompt = """
Based on this financial statement:
1. Analyze the company's financial performance and health
2. Identify key trends in revenue, profitability, and major metrics
3. Calculate and interpret year-over-year growth rates
4. Highlight any red flags or areas of concern
5. Provide a summary of whether this company appears to be a good investment.
In your analysis, be sure to include numbers and percentages for e.g. year over year growth rates.
"""

def analyze_financial_data(ticker):
    """
    Analyze financial statements for a given ticker symbol
    
    Args:
        ticker (str): Stock ticker symbol (e.g., 'TSLA', 'AAPL')
    """
    ticker = ticker.lower()
    output_dir = "outputs"
    
    # Update file paths to use CSV extension
    income_statement_path = os.path.join(output_dir, f"{ticker}_income_statement.csv")
    balance_sheet_path = os.path.join(output_dir, f"{ticker}_balance_sheet.csv")
    cash_flow_path = os.path.join(output_dir, f"{ticker}_cash_flow.csv")
    
    if not os.path.exists(income_statement_path) or not os.path.exists(balance_sheet_path):
        print(f"❌ Financial statements for {ticker.upper()} not found. Please run main.py first.")
        return

    try:
        # Read CSV files instead of binary files
        with open(income_statement_path, "r") as income_file, \
             open(balance_sheet_path, "r") as balance_file, \
             open(cash_flow_path, "r") as cash_flow_file: 
            
            income_data = income_file.read()
            balance_data = balance_file.read()
            cash_flow_data = cash_flow_file.read()
            
        print(f"📊 Analyzing financial statements for {ticker.upper()}...")
        
        # Update the model input to handle text data instead of images
        response = model.generate_content([
            "Analyze these financial statements for " + ticker.upper() + ":",
            income_data,
            "This is the income statement.",
            balance_data,
            "This is the balance sheet.",
            cash_flow_data,
            "This is the cash flow statement.",
            analysis_prompt
        ])

        if response.text:
            print("\n=== Financial Analysis ===\n")
            print(response.text)
            
            # Save analysis to file
            analysis_file = os.path.join(output_dir, f"{ticker}_analysis.txt")
            with open(analysis_file, "w") as f:
                f.write(response.text)
            print(f"\n✅ Analysis saved to {analysis_file}")
        else:
            print("❌ No analysis generated from the model")

    except Exception as e:
        print(f"❌ Error during analysis: {e}")
