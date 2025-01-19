import base64
import os
import google.generativeai as genai
from dotenv import load_dotenv
from enum import Enum
from google.cloud import storage
import json
from google.oauth2 import service_account
import logging
load_dotenv()

logger = logging.getLogger(__name__)

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
    Based on this financial statement, include numbers and percentages for e.g. year over year growth rates
    to answer to the question.
"""

class QuestionType(Enum):
    GENERAL_FINANCE = "general-finance"
    COMPANY_GENERAL = "company-general"
    COMPANY_SPECIFIC_FINANCE = "company-specific-finance"

def classify_question(question):
    """
    Classify the question as either '{QuestionType.GENERAL_FINANCE.value}' or '{QuestionType.COMPANY_SPECIFIC.value}'.
    So that we can determine proper model to use for analysis.
    Return the classification as a string.
    """    

    classification_model = genai.GenerativeModel('gemini-1.5-pro')
    prompt = f"""Classify the following question as either '{QuestionType.GENERAL_FINANCE.value}' or '{QuestionType.COMPANY_SPECIFIC_FINANCE.value}' or '{QuestionType.COMPANY_GENERAL.value}'.
    Examples:
    - 'What is the average P/E ratio for the tech industry?' -> {QuestionType.GENERAL_FINANCE.value}
    - 'What is Apple's revenue for the last quarter?' -> {QuestionType.COMPANY_SPECIFIC_FINANCE.value}
    - 'What is the company's mission statement?' -> {QuestionType.COMPANY_GENERAL.value}
    Question:
    {question}"""

    try:
        response = classification_model.generate_content([prompt])
        if QuestionType.COMPANY_SPECIFIC_FINANCE.value in response.text.lower():
            return QuestionType.COMPANY_SPECIFIC_FINANCE.value
        elif QuestionType.COMPANY_GENERAL.value in response.text.lower():
            return QuestionType.COMPANY_GENERAL.value
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
    logger.info(f"The question is classified as: {classification}")

    if classification == QuestionType.GENERAL_FINANCE.value:
        try:
            general_finance_model = genai.GenerativeModel(
                model_name="gemini-1.5-pro",
                system_instruction="""
                You are a professional financial analyst who specializes in explaining financial concepts.
                Give a short explanation of the financial question in less than 100 words.
                Give an example of how this concept is used in real-world financial scenarios, using well-known companies and their financial statements.
                """
            )
            response = general_finance_model.generate_content([
                "Please explain this financial concept or answer this question:",
                question
            ])
            return {"data": response.text} if response.text else {"data": "❌ No explanation generated"}
        except Exception as e:
            return {"data": f"❌ Error generating explanation: {e}"}

    if classification == QuestionType.COMPANY_GENERAL.value:
        try:
            company_general_model = genai.GenerativeModel(
                model_name="gemini-1.5-pro",
                system_instruction="""
                    You are a professional investor who has a lot of knowledge about companies.
                    You are able to answer questions about companies in general.
                """
            )
            response = company_general_model.generate_content([
                "Please answer this question:",
                question
            ])
            return {"data": response.text} if response.text else {"data": "❌ No explanation generated"}
        except Exception as e:
            return {"data": f"❌ Error generating explanation: {e}"}
    
    # If not generic, proceed with company-specific analysis
    if not ticker:
        # Ask the model to find the ticker
        ticker_model = genai.GenerativeModel(
            model_name="gemini-1.5-pro",
            system_instruction="""
                You are a professional financial analyst who specializes in finding stock tickers.
                Given a company name, find the stock ticker for the company.
            """
        )
        response = ticker_model.generate_content([
            "Please find the stock ticker for the company that is mentioned in the question:",
            question,
            "only return the ticker name without any other texts."
        ])
        ticker = response.text

    # Lower case and strip any whitespace
    ticker = ticker.lower().strip()

    credentials = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
    if not credentials:
        return {"data": "❌ Google Cloud credentials not found in environment variables"}
    
    credentials_dict = json.loads(base64.b64decode(credentials).decode('utf-8'))
    credentials = service_account.Credentials.from_service_account_info(credentials_dict)
    client = storage.Client(credentials=credentials)
    bucket = client.bucket('stock_agent_financial_report')
    
    if not bucket:
        return {"data": "❌ GCP bucket name not configured"}
        
    try:
        # Read files from GCP bucket
        income_blob = bucket.blob(f"{ticker}_income_statement.csv")
        balance_blob = bucket.blob(f"{ticker}_balance_sheet.csv")
        cash_flow_blob = bucket.blob(f"{ticker}_cash_flow.csv")
        
        # TODO: may be do another classification to check which financial statement the question is asking for
        # and then check if the file exists in the bucket and only throw if the relevant statement is missing
        # instead of throwing an error if any of the statements are missing like now
        if not (income_blob.exists() and balance_blob.exists() and cash_flow_blob.exists()):
            return {"data": f"❌ Financial statements for {ticker.upper()} not found in cloud storage."}
        
        income_data = income_blob.download_as_text()
        balance_data = balance_blob.download_as_text()
        cash_flow_data = cash_flow_blob.download_as_text()
        
    except Exception as e:
        return {"data": f"❌ Error accessing cloud storage: {e}"}

    # Continue with analysis using the loaded data
    try:
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

        return {"data": response.text} if response.text else {"data": "❌ No analysis generated from the model"}

    except Exception as e:
        return {"data": f"❌ Error during analysis: {e}"}
