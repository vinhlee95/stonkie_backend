from dotenv import load_dotenv
from enum import Enum
import logging
from agent.agent import Agent
from ai_models.gemini import ContentType
from ai_models.model_name import ModelName
from connectors.company import get_by_ticker
from connectors.company_financial import CompanyFinancialConnector

from external_knowledge.company_fundamental import get_company_fundamental

import time

load_dotenv()

logger = logging.getLogger(__name__)

agent = Agent(model_type="gemini")
company_financial_connector = CompanyFinancialConnector()

analysis_prompt = """
    Based on this financial statement, include numbers and percentages for e.g. year over year growth rates
    to answer to the question.
"""

class QuestionType(Enum):
    GENERAL_FINANCE = "general-finance"
    COMPANY_GENERAL = "company-general"
    COMPANY_SPECIFIC_FINANCE = "company-specific-finance"

async def classify_question(question):
    t_start = time.perf_counter()
    """
    Classify the question as either '{QuestionType.GENERAL_FINANCE.value}' or '{QuestionType.COMPANY_SPECIFIC.value}'.
    So that we can determine proper model to use for analysis.
    Return the classification as a string.
    """    

    prompt = f"""Classify the following question into one of these three categories:
    1. '{QuestionType.GENERAL_FINANCE.value}' - for general financial concepts, market trends, or questions about individuals that don't require specific company financial statements
    2. '{QuestionType.COMPANY_SPECIFIC_FINANCE.value}' - for questions that specifically require analyzing a company's financial statements
    3. '{QuestionType.COMPANY_GENERAL.value}' - for general questions about a company that don't require financial analysis

    Examples:
    - 'What is the average P/E ratio for the tech industry?' -> {QuestionType.GENERAL_FINANCE.value}
    - 'How does inflation affect stock markets?' -> {QuestionType.GENERAL_FINANCE.value}
    - 'How does Bill Gates' charitable giving affect his net worth?' -> {QuestionType.GENERAL_FINANCE.value}
    - 'What is Apple's revenue for the last quarter?' -> {QuestionType.COMPANY_SPECIFIC_FINANCE.value}
    - 'What was Microsoft's profit margin in 2023?' -> {QuestionType.COMPANY_SPECIFIC_FINANCE.value}
    - 'What is Tesla's mission statement?' -> {QuestionType.COMPANY_GENERAL.value}
    - 'Who is the CEO of Amazon?' -> {QuestionType.COMPANY_GENERAL.value}

    Rules:
    - If the question requires analyzing specific company financial statements or metrics, classify as {QuestionType.COMPANY_SPECIFIC_FINANCE.value}
    - If the question is about general market trends, concepts, or individuals, classify as {QuestionType.GENERAL_FINANCE.value}
    - If the question is about company information but doesn't need financial analysis, classify as {QuestionType.COMPANY_GENERAL.value}

    Question to classify:
    {question}"""

    try:
        response = agent.generate_content(
            prompt=[prompt], 
            model_name=ModelName.Gemini25FlashLite,
            stream=False
        )
        
        # Wait for the response to complete
        response_text = response.text
        
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
    finally:
        t_end = time.perf_counter()
        logger.info(f"Profiling classify_question: {t_end - t_start:.4f}s")

async def handle_general_finance_question(question: str, use_google_search: bool):
    t_start = time.perf_counter()
    """Handle questions about general financial concepts."""
    try:
        yield {
            "type": "thinking_status",
            "body": "Structuring the answer..."
        }
        t_model = time.perf_counter()
        for part in agent.generate_content(
            prompt=f"""
                Please explain this financial concept or answer this question:

                {question}.

                Give a short answer in less than 150 words. 
                Break the answer into different paragraphs for better readability. 
                In the last paragraph, give an example of how this concept is used in a real-world situation
            """,
            model_name=ModelName.Gemini25FlashLite,
            stream=True,
            use_google_search=use_google_search,
        ):
            yield {
                "type": "answer",
                "body": part.text
            }
        t_model_end = time.perf_counter()
        logger.info(f"Profiling handle_general_finance_question model_generate_content: {t_model_end - t_model:.4f}s")

        t_related = time.perf_counter()
        prompt = f"""
            Based on this original question: "{question}"
            Generate 3 related but different follow-up questions that users might want to ask next.
            Return only the questions, do not return the number or order of the question.
        """
        response_generator = agent.generate_content_and_normalize_results([prompt], model_name=ModelName.Gemini25FlashLite)
        async for answer in response_generator:
            yield {
                "type": "related_question",
                "body": answer
            }
        t_related_end = time.perf_counter()
        logger.info(f"Profiling handle_general_finance_question related_questions: {t_related_end - t_related:.4f}s")
        logger.info(f"Profiling handle_general_finance_question total: {t_related_end - t_start:.4f}s")
    except Exception as e:
        logger.error(f"❌ Error generating explanation: {e}")
        yield {
            "type": "answer",
            "body": "❌ Error generating explanation. Please try again later."
        }

async def handle_company_general_question(ticker: str, question: str, use_google_search: bool):
    t_start = time.perf_counter()
    """Handle general questions about companies."""
    company = get_by_ticker(ticker)
    company_name = company.name if company else ""

    yield {
        "type": "thinking_status",
        "body": f"Analyzing general information about {company_name} (ticker: {ticker}) and preparing a concise, insightful answer..."
    }

    try:
        prompt = f"""
            You are an expert about a business. Answer the following question about {company_name} (ticker: {ticker}):
            {question}.

            Keep the response concise in under 150 words. Do not repeat points or facts. Connect the facts to a compelling story.
            Break the answer into different paragraphs and bullet points for better readability.
        """
        t_model = time.perf_counter()
        for part in agent.generate_content(
            prompt=prompt, 
            model_name=ModelName.Gemini25FlashLite, 
            stream=True,
            thought=False,
            use_google_search=use_google_search,
        ):
            if part.type == ContentType.Thought:
                yield {
                    "type": "thinking_status",
                    "body": part.text
                }
            elif part.type == ContentType.Answer:
                yield {
                    "type": "answer",
                    "body": part.text
                }
            elif part.type == ContentType.Ground:
                yield {
                    "type": "google_search_ground",
                    "body": part.ground.text,
                    "url": part.ground.uri
                }
            else:
                logger.warning(f"Unknown content part {str(part)}")
        t_model_end = time.perf_counter()
        logger.info(f"Profiling handle_company_general_question model_generate_content: {t_model_end - t_model:.4f}s")

        t_related = time.perf_counter()
        prompt = f"""
            Based on this original question: "{question}"
            Generate 3 related but different follow-up questions that users might want to ask next.
            Return only the questions, do not return the number or order of the question.
        """
        response_generator = agent.generate_content_and_normalize_results([prompt], model_name=ModelName.Gemini25FlashLite)
        async for answer in response_generator:
            yield {
                "type": "related_question",
                "body": answer
            }
        t_related_end = time.perf_counter()
        logger.info(f"Profiling handle_company_general_question related_questions: {t_related_end - t_related:.4f}s")
        logger.info(f"Profiling handle_company_general_question total: {t_related_end - t_start:.4f}s")
    except Exception as e:
        logger.error(f"Error generating answer: {str(e)}")
        yield {
            "type": "answer",
            "body": f"❌ Error generating answer."
        }

async def handle_company_specific_finance(ticker: str, question: str, use_google_search: bool):
    t_start = time.perf_counter()
    ticker = ticker.lower().strip()

    # Get company fundamental data
    yield {
        "type": "thinking_status",
        "body": "Retrieving company fundamental data and financial statements..."
    }
    t_fundamental = time.perf_counter()
    company_fundamental = get_company_fundamental(ticker)
    t_fundamental_end = time.perf_counter()
    logger.info(f"Profiling handle_company_specific_finance get_company_fundamental: {t_fundamental_end - t_fundamental:.4f}s")

    yield {
        "type": "thinking_status",
        "body": "Performing a comprehensive analysis. This might take a moment, but the insights will be worth the wait..."
    }
    
    try:
        t_statements = time.perf_counter()
        annual_financial_statements = [
            CompanyFinancialConnector.to_dict(item) for item in company_financial_connector.get_company_financial_statements(ticker)
        ]
        quarterly_financial_statements = [
            CompanyFinancialConnector.to_dict(item) for item in company_financial_connector.get_company_quarterly_financial_statements(ticker)
        ]
        t_statements_end = time.perf_counter()
        logger.info(f"Profiling handle_company_specific_finance get_financial_statements: {t_statements_end - t_statements:.4f}s")

        t_model = time.perf_counter()
        financial_context = f"""
            You are a seasoned financial analyst. Your task is to provide an insightful, non-repetitive analysis for the following question, based on the provided financial data and broader market context.

            Question: {question}

            Here is the financial data for {ticker.upper()}:
            Company Fundamental Data:
            {company_fundamental}

            Annual Financial Statements:
            {annual_financial_statements}
            
            Quarterly Financial Statements:
            {quarterly_financial_statements}
            
            **Instructions for your analysis:**

            1.  **Summary (approx. 50 words):** Start with a concise summary of your key findings. This should be a high-level overview.

            2.  **Detailed Analysis (approx. 100-150 words):**
                *   **Financial Performance:** Analyze key metrics from the statements (like revenue, net income, and profit margins). Go beyond just stating numbers. Explain year-over-year growth/decline and what it signifies about the company's health and strategy.
                *   **Insightful Observations:** Don't just state facts. Provide insights. For example, if revenue grew, what might be the driving factors? If margins shrunk, what could be the cause?
                *   **Industry Context & Trends:** Use your knowledge and the search tool to compare the company's performance against its industry peers and broader market trends. Is the company outperforming or underperforming the market? Are there any significant industry trends (e.g., new technology, regulatory changes, consumer behavior shifts) impacting the company?

            **Crucial Rules to Follow:**
            - **NO DUPLICATION:** Do not repeat the same points or numbers across different sections. Each sentence should add new information or a new perspective.
            - **SYNTHESIZE, DON'T JUST LIST:** Connect the dots between different data points to form a coherent narrative.
            - **BE INSIGHTFUL:** Provide analysis, not just a summary of data. Explain the 'so what' behind the numbers.
            - **USE SEARCH WISELY:** Use the Google Search tool to get up-to-date context, especially for industry trends and competitive analysis. Prioritize reputable financial news sources.
            - **CONCISE:** Keep the entire response under 200 words.
        """
        for part in agent.generate_content(
            [
                financial_context,
                analysis_prompt,
            ], 
            model_name=ModelName.GeminiFlash, 
            stream=True, 
            thought=True,
            use_google_search=use_google_search,
        ):
            if part.type == ContentType.Thought:
                yield {
                    "type": "thinking_status",
                    "body": part.text
                }
            elif part.type == ContentType.Answer:
                yield {
                    "type": "answer",
                    "body": part.text if part.text else "❌ No analysis generated from the model"
                }
            elif part.type == ContentType.Ground:
                yield {
                    "type": "google_search_ground",
                    "body": part.ground.text,
                    "url": part.ground.uri
                }
            else:
                logger.warning(f'Unknown content part {str(part)}')
        t_model_end = time.perf_counter()
        logger.info(f"Profiling handle_company_specific_finance model_generate_content: {t_model_end - t_model:.4f}s")

        t_related = time.perf_counter()
        prompt = f"""
            Based on this original question: "{question}"
            Generate 3 related but different follow-up questions that users might want to ask next.
            These questions should be related to either balance sheet, income statement or cash flow statement.
            Return only the questions, do not return the number or order of the question.
        """
        response = agent.generate_content_and_normalize_results([prompt], model_name=ModelName.Gemini25FlashLite)
        async for answer in response:
            yield {
                "type": "related_question",
                "body": answer
            }
        t_related_end = time.perf_counter()
        logger.info(f"Profiling handle_company_specific_finance related_questions: {t_related_end - t_related:.4f}s")
        logger.info(f"Profiling handle_company_specific_finance total: {t_related_end - t_start:.4f}s")
    except Exception as e:
        logger.error(f"Error during analysis: {e}")
        yield {
            "type": "answer",
            "body": "Error during analysis. Please try again later."
        }

async def analyze_financial_data_from_question(ticker: str, question: str, use_google_search: bool):
    """
    Analyze financial statements for a given ticker symbol or answer generic financial questions
    
    Args:
        ticker (str): Stock ticker symbol (e.g., 'TSLA', 'AAPL')
        question (str): Specific question about the financial data or generic financial concept
        
    Yields:
        str: Chunks of analysis response as they are generated
    """
    t_start = time.perf_counter()
    
    yield {
        "type": "thinking_status",
        "body": "Just a moment..."
    }

    classification = await classify_question(question)
    logger.info(f"The question is classified as: {classification}")

    if use_google_search:
        yield {
            "type": "thinking_status",
            "body": "Using Google Search to get up-to-date information. This might take a bit longer, but it will help you get a better answer."
        }

    handlers = {
        QuestionType.GENERAL_FINANCE.value: lambda: handle_general_finance_question(question, use_google_search),
        QuestionType.COMPANY_GENERAL.value: lambda: handle_company_general_question(ticker, question, use_google_search),
        QuestionType.COMPANY_SPECIFIC_FINANCE.value: lambda: handle_company_specific_finance(ticker, question, use_google_search)
    }

    handler = handlers.get(classification)
    if handler:
        t_handler = time.perf_counter()
        async for chunk in handler():
            yield chunk
        t_handler_end = time.perf_counter()
        logger.info(f"Profiling analyze_financial_data_from_question handler: {t_handler_end - t_handler:.4f}s")
    else:
        yield "❌ Unable to classify question type"
    t_end = time.perf_counter()
    logger.info(f"Profiling analyze_financial_data_from_question total: {t_end - t_start:.4f}s")
