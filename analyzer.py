import base64
import os
from dotenv import load_dotenv
from enum import Enum
from google.cloud import storage
import json
from google.oauth2 import service_account
import logging
from agent.agent import Agent
import pandas as pd
from io import StringIO
from typing import Dict, Any

load_dotenv()

logger = logging.getLogger(__name__)

agent = Agent(model_type="gemini")


analysis_prompt = """
    Based on this financial statement, include numbers and percentages for e.g. year over year growth rates
    to answer to the question.
"""

class QuestionType(Enum):
    GENERAL_FINANCE = "general-finance"
    COMPANY_GENERAL = "company-general"
    COMPANY_SPECIFIC_FINANCE = "company-specific-finance"

async def classify_question(question):
    """
    Classify the question as either '{QuestionType.GENERAL_FINANCE.value}' or '{QuestionType.COMPANY_SPECIFIC.value}'.
    So that we can determine proper model to use for analysis.
    Return the classification as a string.
    """    

    prompt = f"""Classify the following question as either '{QuestionType.GENERAL_FINANCE.value}' or '{QuestionType.COMPANY_SPECIFIC_FINANCE.value}' or '{QuestionType.COMPANY_GENERAL.value}'.
    Examples:
    - 'What is the average P/E ratio for the tech industry?' -> {QuestionType.GENERAL_FINANCE.value}
    - 'What is Apple's revenue for the last quarter?' -> {QuestionType.COMPANY_SPECIFIC_FINANCE.value}
    - 'What is the company's mission statement?' -> {QuestionType.COMPANY_GENERAL.value}
    Question:
    {question}"""

    try:
        response = await agent.generate_content([prompt])
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

async def analyze_financial_data_from_question(ticker, question):
    """
    Analyze financial statements for a given ticker symbol or answer generic financial questions
    
    Args:
        ticker (str): Stock ticker symbol (e.g., 'TSLA', 'AAPL')
        question (str): Specific question about the financial data or generic financial concept
        
    Yields:
        str: Chunks of analysis response as they are generated
    """
    classification = await classify_question(question)
    logger.info(f"The question is classified as: {classification}")

    if classification == QuestionType.GENERAL_FINANCE.value:
        try:
            response = await agent.generate_content([
                "Please explain this financial concept or answer this question:",
                question
            ], stream=True)

            async for chunk in response:
                yield chunk.text if chunk.text else "❌ No explanation generated"
            return
        except Exception as e:
            yield f"❌ Error generating explanation: {e}"
            return

    if classification == QuestionType.COMPANY_GENERAL.value:
        try:
            response = await agent.generate_content([
                "Please answer this question:",
                question
            ])

            async for chunk in response:
                yield chunk.text if chunk.text else "❌ No explanation generated"
            return
        except Exception as e:
            yield f"❌ Error generating explanation: {e}"
            return
    
    # If not generic, proceed with company-specific analysis
    if not ticker:
        response = await agent.generate_content([
            "Please find the stock ticker for the company that is mentioned in the question:",
            question,
            "only return the ticker name without any other texts."
        ])

        async for chunk in response:
            yield chunk.text if chunk.text else "❌ No ticker found"
        return

    # Lower case and strip any whitespace
    ticker = ticker.lower().strip()

    credentials = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
    if not credentials:
        yield "❌ Google Cloud credentials not found in environment variables"
        return
    
    credentials_dict = json.loads(base64.b64decode(credentials).decode('utf-8'))
    credentials = service_account.Credentials.from_service_account_info(credentials_dict)
    client = storage.Client(credentials=credentials)
    bucket = client.bucket('stock_agent_financial_report')
    
    if not bucket:
        yield "❌ GCP bucket name not configured"
        return
        
    try:
        # Read files from GCP bucket and convert to pandas DataFrames
        income_blob = bucket.blob(f"{ticker}_income_statement.csv")
        balance_blob = bucket.blob(f"{ticker}_balance_sheet.csv")
        cash_flow_blob = bucket.blob(f"{ticker}_cash_flow.csv")
        
        if not (income_blob.exists() and balance_blob.exists() and cash_flow_blob.exists()):
            yield f"❌ Financial statements for {ticker.upper()} not found in cloud storage."
            return
        
        income_df = pd.read_csv(StringIO(income_blob.download_as_text()))
        balance_df = pd.read_csv(StringIO(balance_blob.download_as_text()))
        cash_flow_df = pd.read_csv(StringIO(cash_flow_blob.download_as_text()))
        
        # Convert DataFrames to structured dictionaries
        financial_data = {
            'income_statement': format_financial_data(income_df),
            'balance_sheet': format_financial_data(balance_df),
            'cash_flow': format_financial_data(cash_flow_df)
        }
        
        # Create a structured prompt with the data
        financial_context = f"""Here are the financial statements for {ticker.upper()}:
            Income Statement:
            {json.dumps(financial_data['income_statement'], indent=2)}

            Balance Sheet:
            {json.dumps(financial_data['balance_sheet'], indent=2)}

            Cash Flow Statement:
            {json.dumps(financial_data['cash_flow'], indent=2)}

            Please analyze the data with these guidelines:
            1. Use specific numbers from the statements
            2. Calculate year-over-year changes when relevant
            3. Present growth rates as percentages
            5. Ensure numerical consistency across years
        """

    except Exception as e:
        yield f"❌ Error accessing cloud storage: {e}"
        return

    # Continue with analysis using the structured data
    try:
        response = await agent.generate_content([
            financial_context,
            analysis_prompt,
            f"\nSpecific question to address: {question}"
        ])

        async for chunk in response:
            yield chunk.text if chunk.text else "❌ No analysis generated from the model"

    except Exception as e:
        yield f"❌ Error during analysis: {e}"

def format_financial_data(df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    """Convert DataFrame to structured dictionary with years as keys and metrics as nested keys."""
    data_dict = {}
    # Assuming first column is metric names and other columns are years
    for year in df.columns[1:]:
        data_dict[str(year)] = {}
        for metric in df.iloc[:, 0].values:
            value = df.loc[df.iloc[:, 0] == metric, year].iloc[0]
            # Convert to float if numeric, otherwise keep as string
            try:
                value = float(value)
            except (ValueError, TypeError):
                pass
            data_dict[str(year)][str(metric)] = value
    return data_dict
