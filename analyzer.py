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

# Add this near the top of the file with other global variables
_financial_data_cache: dict[str, dict[str, str]] = {}

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
        # Wait for the response to complete
        await response.resolve()
        response_text = response.text.lower()
        
        if QuestionType.COMPANY_SPECIFIC_FINANCE.value in response_text:
            return QuestionType.COMPANY_SPECIFIC_FINANCE.value
        elif QuestionType.COMPANY_GENERAL.value in response_text:
            return QuestionType.COMPANY_GENERAL.value
        elif QuestionType.GENERAL_FINANCE.value in response_text:
            return QuestionType.GENERAL_FINANCE.value
        else:
            raise ValueError(f"Unknown question type: {response_text}")
    except Exception as e:
        print(f"Error during classifying type of question: {e}")
        return None

async def get_financial_data_for_ticker(ticker: str) -> dict[str, str] | None:
    """
    Retrieve and format financial data for a given ticker from cloud storage.
    Data is cached in memory for 1 hour to reduce API calls.
    
    Args:
        ticker (str): Stock ticker symbol (lowercase)
        
    Returns:
        Dict containing formatted financial statements or None if data not found
    
    Raises:
        Exception: If there's an error accessing or processing the data
    """
    # Check cache first
    # TODO: implement some key/value caching store 
    if ticker in _financial_data_cache:
        return _financial_data_cache[ticker]
    
    logger.info(f"Fetch financial data from cloud storage for {ticker}")
    credentials = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
    if not credentials:
        return None
    
    credentials_dict = json.loads(base64.b64decode(credentials).decode('utf-8'))
    credentials = service_account.Credentials.from_service_account_info(credentials_dict)
    client = storage.Client(credentials=credentials)
    bucket = client.bucket('stock_agent_financial_report')
    
    if not bucket:
        return None
    
    income_blob = bucket.blob(f"{ticker}_income_statement.csv")
    balance_blob = bucket.blob(f"{ticker}_balance_sheet.csv")
    cash_flow_blob = bucket.blob(f"{ticker}_cash_flow.csv")
    
    if not (income_blob.exists() and balance_blob.exists() and cash_flow_blob.exists()):
        return None
    
    try:
        income_df = pd.read_csv(StringIO(income_blob.download_as_text()))
        balance_df = pd.read_csv(StringIO(balance_blob.download_as_text()))
        cash_flow_df = pd.read_csv(StringIO(cash_flow_blob.download_as_text()))
        
        # Format the data
        result = {
            'income_statement': json.dumps(format_financial_data(income_df), indent=2),
            'balance_sheet': json.dumps(format_financial_data(balance_df), indent=2),
            'cash_flow': json.dumps(format_financial_data(cash_flow_df), indent=2)
        }
        
        # Store in cache
        _financial_data_cache[ticker] = result
        return result
        
    except Exception as e:
        logger.error(f"Error retrieving financial data for {ticker}: {e}")
        return None

async def handle_general_finance_question(question):
    """Handle questions about general financial concepts."""
    try:
        response_generator = agent.generate_content_and_normalize_results([
            "Please explain this financial concept or answer this question:",
            question,
            "Give a short answer in less than 100 words. Also give an example of how this concept is used in a real-world situation."
        ])

        async for answer in response_generator:
            yield {
                "type": "answer",
                "body": answer
            }
    except Exception as e:
        logger.error(f"❌ Error generating explanation: {e}")
        yield {
            "type": "answer",
            "body": "❌ Error generating explanation. Please try again later."
        }

async def handle_company_general_question(question):
    """Handle general questions about companies."""
    try:
        response = await agent.generate_content([
            "Please answer this question about general company information:",
            question
        ], stream=True)

        async for chunk in response:
            yield chunk.text if chunk.text else "❌ No explanation generated"

    except Exception as e:
        yield f"❌ Error generating explanation: {e}"

async def handle_company_specific_finance(ticker, question):
    """Handle company-specific financial questions."""
    if not ticker:
        response = await agent.generate_content([
            "Please find the stock ticker for the company that is mentioned in the question:",
            question,
            "only return the ticker name without any other texts."
        ])
        async for chunk in response:
            yield chunk.text if chunk.text else "❌ No ticker found"
        return

    ticker = ticker.lower().strip()
    try:
        financial_data = await get_financial_data_for_ticker(ticker)
        if financial_data is None:
            yield f"❌ Financial statements for {ticker.upper()} not found in cloud storage."
            return

        financial_context = f"""Here are the financial statements for {ticker.upper()}:
            Income Statement:
            {financial_data.get('income_statement', {})}

            Balance Sheet:
            {financial_data.get('balance_sheet', {})}

            Cash Flow Statement:
            {financial_data.get('cash_flow', {})}

            Please analyze the data with these guidelines:
            1. Use specific numbers from the statements
            2. Calculate year-over-year changes when relevant
            3. Present growth rates as percentages
            5. Ensure numerical consistency across years
        """

        response = await agent.generate_content([
            financial_context,
            analysis_prompt,
            f"\nSpecific question to address: {question}"
        ], stream=True)

        async for chunk in response:
            yield {
                "type": "answer",
                "body": chunk.text if chunk.text else "❌ No analysis generated from the model"
            }
            
        # Add related questions after main response
        prompt = f"""
            Based on this original question: "{question}"
            Generate 3 related but different follow-up questions that users might want to ask next.
            These questions should be related to either balance sheet, income statement or cash flow statement.
            Return only the questions, do not return the number or order of the question.
            Do not yield individual words, yield the full question in a line once you have it.
        """

        response = await agent.generate_content([prompt])
        current_question = ""
        question_number = 1
        
        async for chunk in response:
            if chunk.text:
                current_question += chunk.text
                if "\n" in current_question:
                    # Split on newlines and process complete questions
                    parts = current_question.split("\n")
                    # Process all complete questions except the last part
                    for part in parts[:-1]:
                        if part.strip():
                            clean_question = part.replace("*", "").strip()
                            yield {
                                "type": "related_question",
                                "number": question_number,
                                "body": clean_question
                            }
                            question_number += 1
                    # Keep the incomplete part
                    current_question = parts[-1]
        
        # Handle the last question if there is one
        if current_question.strip():
            clean_question = current_question.replace("*", "").strip()
            yield {
                "type": "related_question",
                "number": question_number,
                "body": clean_question
            }

    except Exception as e:
        logger.error(f"Error during analysis: {e}")
        yield {
            "type": "answer",
            "body": "Error during analysis. Please try again later."
        }

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

    handlers = {
        QuestionType.GENERAL_FINANCE.value: lambda: handle_general_finance_question(question),
        QuestionType.COMPANY_GENERAL.value: lambda: handle_company_general_question(question),
        QuestionType.COMPANY_SPECIFIC_FINANCE.value: lambda: handle_company_specific_finance(ticker, question)
    }

    handler = handlers.get(classification)
    if handler:
        async for chunk in handler():
            yield chunk
    else:
        yield "❌ Unable to classify question type"

def format_financial_data(df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    """Convert DataFrame to structured dictionary with years as keys and metrics as nested keys."""
    data_dict = {}
    try:
        # Ensure DataFrame is not empty
        if df.empty:
            logger.error("Empty DataFrame provided to format_financial_data")
            return data_dict

        # Get year columns (all columns except the first one)
        year_columns = df.columns[1:] if len(df.columns) > 1 else []
        
        # Get metric names from first column
        metrics = df.iloc[:, 0].values if len(df.columns) > 0 else []
        
        for year in year_columns:
            data_dict[str(year)] = {}
            for metric in metrics:
                try:
                    # Use boolean indexing instead of loc with multiple conditions
                    mask = df.iloc[:, 0] == metric
                    if any(mask):
                        value = df.loc[mask, year].iloc[0]
                        # Convert to float if numeric, otherwise keep as string
                        try:
                            value = float(value)
                        except (ValueError, TypeError):
                            pass
                        data_dict[str(year)][str(metric)] = value
                except IndexError as e:
                    logger.error(f"IndexError processing metric {metric} for year {year}: {e}")
                except Exception as e:
                    logger.error(f"Error processing metric {metric} for year {year}: {e}")
                    
    except Exception as e:
        logger.error(f"Error in format_financial_data: {e}")
        
    return data_dict
