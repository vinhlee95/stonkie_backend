from dotenv import load_dotenv
from enum import Enum, StrEnum
import logging
from typing import Optional, List, Dict, Any, Tuple
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

class FinancialDataRequirement(StrEnum):
    NONE = "none"  # Can be answered without financial data
    BASIC = "basic"  # Needs only fundamental data (market cap, P/E, etc.)
    DETAILED = "detailed"  # Requires full financial statements

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
        response_text = ""
        try:
            # Try to access the text attribute directly
            response_text = getattr(response, 'text', '')
        except:
            pass
        
        # If no text found, try iterating through response parts
        if not response_text:
            try:
                for part in response:
                    if hasattr(part, 'text'):
                        response_text += part.text
            except:
                pass
        
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

async def classify_financial_data_requirement(ticker: str, question: str) -> FinancialDataRequirement:
    """
    Determine what level of financial data is needed to answer the question.
    
    Args:
        ticker: Company ticker symbol
        question: The question being asked
        
    Returns:
        FinancialDataRequirement: Level of financial data needed
    """
    t_start = time.perf_counter()
    
    prompt = f"""Analyze this question about {ticker.upper()} and determine what level of financial data is needed:
        Question: "{question}"

        Classify into one of these categories:

        1. 'none' - Question can be answered without any financial data (e.g., "What does {ticker.upper()} do?", "Who is the CEO?", "What industry is {ticker.upper()} in?")

        2. 'basic' - Question needs only basic company metrics like market cap, P/E ratio, basic ratios (e.g., "What is {ticker.upper()}'s market cap?", "What's the P/E ratio?", "Is {ticker.upper()} profitable?")

        3. 'detailed' - Question requires specific financial statement data like revenue, expenses, cash flow details (e.g., "What was {ticker.upper()}'s revenue last quarter?", "How much debt does {ticker.upper()} have?", "What's the operating margin trend?")

        Examples:
        - "What does Apple do?" -> none
        - "Who is Tesla's CEO?" -> none  
        - "What is Microsoft's market cap?" -> basic
        - "Is Amazon profitable?" -> basic
        - "What was Apple's revenue in Q3 2024?" -> detailed
        - "How much cash does Tesla have?" -> detailed
        - "What's Google's debt-to-equity ratio?" -> detailed

        Return only the classification: none, basic, or detailed
    """

    try:
        response = agent.generate_content(
            prompt=[prompt], 
            model_name=ModelName.Gemini25FlashLite,
            stream=False
        )
        
        response_text = ""
        try:
            # Try to access the text attribute directly
            response_text = getattr(response, 'text', '').lower().strip()
        except:
            pass
        
        # If no text found, try iterating through response parts
        if not response_text:
            try:
                for part in response:
                    if hasattr(part, 'text'):
                        response_text += part.text
                response_text = response_text.lower().strip()
            except:
                pass
        
        if "detailed" in response_text:
            return FinancialDataRequirement.DETAILED
        elif "basic" in response_text:
            return FinancialDataRequirement.BASIC
        else:
            return FinancialDataRequirement.NONE
            
    except Exception as e:
        logger.error(f"Error classifying financial data requirement: {e}")
        # Default to basic to be safe
        return FinancialDataRequirement.BASIC
    finally:
        t_end = time.perf_counter()
        logger.info(f"Profiling classify_financial_data_requirement: {t_end - t_start:.4f}s")

def _build_financial_context(
    ticker: str,
    question: str,
    data_requirement: FinancialDataRequirement,
    company_fundamental: Optional[Dict[str, Any]],
    annual_statements: List[Dict[str, Any]],
    quarterly_statements: List[Dict[str, Any]]
) -> str:
    """
    Build the appropriate financial context prompt based on data requirement level.
    """
    
    base_context = f"""
        You are a seasoned financial analyst. Your task is to provide an insightful, non-repetitive analysis for the following question.

        Question: {question}
        Company: {ticker.upper()}
    """

    if data_requirement == FinancialDataRequirement.NONE:
        return f"""
            {base_context}
            
            This is a general question about {ticker.upper()} that doesn't require financial data analysis.
            Provide a clear, informative answer using your general knowledge about the company.
            Keep the response under 150 words and make it engaging.
            Use Google Search to get the most up-to-date information if needed.
        """
    
    elif data_requirement == FinancialDataRequirement.BASIC:
        return f"""
            {base_context}
            
            Company Fundamental Data:
            {company_fundamental}
            
            This question requires basic financial metrics. Use the fundamental data provided to answer the question.
            Focus on key metrics like market cap, P/E ratio, basic profitability, and market performance.
            Keep the response concise (under 150 words) but insightful.
            Use Google Search for additional context if needed.
        """
    
    else:  # DETAILED
        return f"""
            {base_context}
            
            Company Fundamental Data:
            {company_fundamental}

            Annual Financial Statements:
            {annual_statements}
            
            Quarterly Financial Statements:
            {quarterly_statements}
            
            **Instructions for your analysis:**

            1. **Summary (approx. 50 words):** Start with a concise summary of your key findings.

            2. **Detailed Analysis (approx. 100-150 words):**
               - **Financial Performance:** Analyze key metrics from the statements (revenue, net income, profit margins)
               - **Insightful Observations:** Explain year-over-year growth/decline and what it signifies
               - **Industry Context & Trends:** Compare against industry peers and market trends

            **Rules:**
            - NO DUPLICATION: Each sentence should add new information
            - BE INSIGHTFUL: Provide analysis, not just data summary
            - USE SEARCH WISELY: Get up-to-date context for industry trends
            - CONCISE: Keep entire response under 200 words
            - INCLUDE SOURCES: Specify sources at the end
        """

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
            Make sure related questions are short and concise. Ideally, less than 15 words each.
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
            Make sure to specify the source of the answer at the end of the analysis.
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
                    "body": part.ground.text if part.ground else "",
                    "url": part.ground.uri if part.ground else ""
                }
            else:
                logger.warning(f"Unknown content part {str(part)}")
        t_model_end = time.perf_counter()
        logger.info(f"Profiling handle_company_general_question model_generate_content: {t_model_end - t_model:.4f}s")

        t_related = time.perf_counter()
        prompt = f"""
            Based on this original question: "{question}"
            Generate 3 related but different follow-up questions that users might want to ask next.
            Make sure related questions are short and concise. Ideally, less than 15 words each.
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

    # First, determine what financial data we actually need
    yield {
        "type": "thinking_status",
        "body": "Analyzing question to determine required data..."
    }
    
    data_requirement = await classify_financial_data_requirement(ticker, question)
    logger.info(f"Financial data requirement: {data_requirement}")

    # Always get basic company data (lightweight) if basic or detailed is needed
    t_fundamental = time.perf_counter()
    company_fundamental = None
    if data_requirement in [FinancialDataRequirement.BASIC, FinancialDataRequirement.DETAILED]:
        yield {
            "type": "thinking_status",
            "body": "Retrieving company fundamental data..."
        }
        company_fundamental = get_company_fundamental(ticker)
    t_fundamental_end = time.perf_counter()
    logger.info(f"Profiling get_company_fundamental: {t_fundamental_end - t_fundamental:.4f}s")

    # Only fetch detailed financial statements if really needed
    annual_financial_statements: List[Dict[str, Any]] = []
    quarterly_financial_statements: List[Dict[str, Any]] = []
    
    if data_requirement == FinancialDataRequirement.DETAILED:
        yield {
            "type": "thinking_status",
            "body": "Retrieving detailed financial statements for comprehensive analysis..."
        }
        
        t_statements = time.perf_counter()
        annual_financial_statements = [
            CompanyFinancialConnector.to_dict(item) 
            for item in company_financial_connector.get_company_financial_statements(ticker)
        ]
        quarterly_financial_statements = [
            CompanyFinancialConnector.to_dict(item) 
            for item in company_financial_connector.get_company_quarterly_financial_statements(ticker)
        ]
        t_statements_end = time.perf_counter()
        logger.info(f"Profiling get_financial_statements: {t_statements_end - t_statements:.4f}s")

    yield {
        "type": "thinking_status", 
        "body": "Analyzing data and preparing insights..."
    }

    try:
        # Create dynamic prompt based on available data
        financial_context = _build_financial_context(
            ticker=ticker,
            question=question,
            data_requirement=data_requirement,
            company_fundamental=company_fundamental,
            annual_statements=annual_financial_statements,
            quarterly_statements=quarterly_financial_statements
        )

        t_model = time.perf_counter()
        for part in agent.generate_content(
            [financial_context, analysis_prompt], 
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
                    "body": part.ground.text if part.ground else "",
                    "url": part.ground.uri if part.ground else ""
                }
            else:
                logger.warning(f'Unknown content part {str(part)}')
        
        t_model_end = time.perf_counter()
        logger.info(f"Profiling model_generate_content: {t_model_end - t_model:.4f}s")

        # Generate related questions
        t_related = time.perf_counter()
        prompt = f"""
            Based on this original question: "{question}"
            Generate 3 related but different follow-up questions that users might want to ask next.
            Make sure related questions are short and concise. Ideally, less than 15 words each.
            Return only the questions, do not return the number or order of the question.
        """
        response = agent.generate_content_and_normalize_results([prompt], model_name=ModelName.Gemini25FlashLite)
        async for answer in response:
            yield {
                "type": "related_question",
                "body": answer
            }
        t_related_end = time.perf_counter()
        logger.info(f"Profiling related_questions: {t_related_end - t_related:.4f}s")
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

    handler = handlers.get(classification) if classification else None
    if handler:
        t_handler = time.perf_counter()
        async for chunk in handler():
            yield chunk
        t_handler_end = time.perf_counter()
        logger.info(f"Profiling analyze_financial_data_from_question handler: {t_handler_end - t_handler:.4f}s")
    else:
        yield {
            "type": "answer",
            "body": "❌ Unable to classify question type"
        }
    t_end = time.perf_counter()
    logger.info(f"Profiling analyze_financial_data_from_question total: {t_end - t_start:.4f}s")
