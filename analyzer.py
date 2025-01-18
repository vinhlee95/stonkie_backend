import os
import google.generativeai as genai
from dotenv import load_dotenv
from enum import Enum

load_dotenv()

# Configure Gemini
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# Very powerful model for investment advice, do not use this yet
super_model = genai.GenerativeModel(
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

model = genai.GenerativeModel(
    model_name="gemini-1.5-pro",
    system_instruction="""
    You are a professional financial analyst who specializes in analyzing company financial statements.
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

class QuestionType(Enum):
    GENERAL_FINANCE = "general-finance"
    COMPANY_SPECIFIC = "company-specific"

def classify_question(question):
    """
    Classify the question as either '{QuestionType.GENERAL_FINANCE.value}' or '{QuestionType.COMPANY_SPECIFIC.value}'.
    So that we can determine proper model to use for analysis.
    Return the classification as a string.
    """    

    classification_model = genai.GenerativeModel('gemini-1.5-pro')
    prompt = f"""Classify the following question as either '{QuestionType.GENERAL_FINANCE.value}' or '{QuestionType.COMPANY_SPECIFIC.value}'.
    Examples:
    - 'What is the average P/E ratio for the tech industry?' -> {QuestionType.GENERAL_FINANCE.value}
    - 'What is Apple's revenue for the last quarter?' -> {QuestionType.COMPANY_SPECIFIC.value}
    Question:
    {question}"""

    try:
        response = classification_model.generate_content([prompt])
        if QuestionType.COMPANY_SPECIFIC.value in response.text.lower():
            return QuestionType.COMPANY_SPECIFIC.value
        elif QuestionType.GENERAL_FINANCE.value in response.text.lower():
            return QuestionType.GENERAL_FINANCE.value
        else:
            raise ValueError(f"Unknown question type: {response.text}")
    except Exception as e:
        print(f"Error during classifying type of question: {e}")
        return None

def analyze_financial_data_from_question(ticker, question):
    """
    Analyze financial statements for a given ticker symbol or answer generic financial questions
    
    Args:
        ticker (str): Stock ticker symbol (e.g., 'TSLA', 'AAPL')
        question (str): Specific question about the financial data or generic financial concept
        
    Returns:
        dict: Object containing the analysis response {"data": str}
    """

    classification = classify_question(question)
    if classification == QuestionType.GENERAL_FINANCE.value:
        try:
            generic_model = genai.GenerativeModel(
                model_name="gemini-1.5-pro",
                system_instruction="""
                You are a professional financial analyst who specializes in explaining financial concepts.
                Give a short explanation of the financial question in less than 100 words.
                Give an example of how this concept is used in real-world financial scenarios, using well-known companies and their financial statements.
                """
            )
            response = generic_model.generate_content([
                "Please explain this financial concept or answer this question:",
                question
            ])
            return {"data": response.text} if response.text else {"data": "❌ No explanation generated"}
        except Exception as e:
            return {"data": f"❌ Error generating explanation: {e}"}
    
    # If not generic, proceed with company-specific analysis
    ticker = ticker.lower()
    output_dir = "outputs"
    
    # Update file paths to use CSV extension
    income_statement_path = os.path.join(output_dir, f"{ticker}_income_statement.csv")
    balance_sheet_path = os.path.join(output_dir, f"{ticker}_balance_sheet.csv")
    cash_flow_path = os.path.join(output_dir, f"{ticker}_cash_flow.csv")
    
    if not os.path.exists(income_statement_path) or not os.path.exists(balance_sheet_path):
        return {"data": f"❌ Financial statements for {ticker.upper()} not found. Please run main.py first."}

    try:
        # Read CSV files instead of binary files
        with open(income_statement_path, "r") as income_file, \
             open(balance_sheet_path, "r") as balance_file, \
             open(cash_flow_path, "r") as cash_flow_file: 
            
            income_data = income_file.read()
            balance_data = balance_file.read()
            cash_flow_data = cash_flow_file.read()
        
        # Update the model input to include the specific question
        response = model.generate_content([
            f"Here are financial statements for {ticker.upper()}:",
            income_data,
            "This is the income statement.",
            balance_data,
            "This is the balance sheet.",
            cash_flow_data,
            "This is the cash flow statement.",
            analysis_prompt,
            f"\nSpecific question to address: {question}"
        ])

        if response.text:
            return {"data": response.text}
        else:
            return {"data": "❌ No analysis generated from the model"}

    except Exception as e:
        return {"data": f"❌ Error during analysis: {e}"}
