from dotenv import load_dotenv
from enum import Enum
import logging
from agent.agent import Agent
from ai_models.model_name import ModelName
from connectors.vector_store import search_similar_content_and_format_to_texts
from connectors.company import get_by_ticker
from connectors.company_financial import CompanyFinancialConnector

from external_knowledge.company_fundamental import get_company_fundamental

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
            model_name=ModelName.GeminiFlashLite,
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

async def handle_general_finance_question(question):
    """Handle questions about general financial concepts."""
    try:
        yield {
            "type": "thinking_status",
            "body": "Structuring the answer..."
        }
        response_generator = agent.generate_content_and_normalize_results(
            prompt=[
                "Please explain this financial concept or answer this question:",
                question,
                "Give a short answer in less than 100 words. Also give an example of how this concept is used in a real-world situation."
            ],
            model_name=ModelName.GeminiFlashLite,
        )

        async for answer in response_generator:
            yield {
                "type": "answer",
                "body": answer
            }

        prompt = f"""
            Based on this original question: "{question}"
            Generate 3 related but different follow-up questions that users might want to ask next.
            Return only the questions, do not return the number or order of the question.
        """

        response_generator = agent.generate_content_and_normalize_results([prompt], model_name=ModelName.GeminiFlashLite)

        async for answer in response_generator:
            yield {
                "type": "related_question",
                "body": answer
            }
    except Exception as e:
        logger.error(f"❌ Error generating explanation: {e}")
        yield {
            "type": "answer",
            "body": "❌ Error generating explanation. Please try again later."
        }

COMPANY_DOCUMENT_INDEX_NAME = "company10k"
LENGTH_LIMIT_PROMPT = "Try to make the answer as concise as possible. Ideally bellow 300 words."

async def handle_company_general_question(ticker, question):
    """Handle general questions about companies."""
    company = get_by_ticker(ticker)
    company_name = company.name if company else ""

    try:
        openai_agent = Agent(model_type="openai")
        # Format search results into financial context

        context_from_official_document = search_similar_content_and_format_to_texts(
            query_embeddings=openai_agent.generate_embedding(question),
            index_name=COMPANY_DOCUMENT_INDEX_NAME,
            filter={"ticker": ticker.lower()}
        )
        if context_from_official_document:
            prompt = [
                f"Answer this question from company {company_name} with ticker {ticker}:",
                question,
                f"Here are relevant information from 10K document: {context_from_official_document}",
                "Use the relevant information from 10K document first when answering the question. Make sure that the answer includes all the facts given by the 10K document.",
                "Make the answer as details as possible, including all the facts from the 10K document.",
                "At the end of the answer, state clearly which section and page from 10K document the answer bases on.",
                LENGTH_LIMIT_PROMPT
            ]
        else:
            prompt = [
                f"Answer this question from company {company_name} with ticker {ticker}:",
                question,
                "Use your general knowledge and Google search results to answer the question.",
                "Make the answer as details as possible, including all the facts.",
                "At the end of the answer, state clearly which specific website and their URL do you get the information from.",
                LENGTH_LIMIT_PROMPT
            ]

        yield {
            "type": "thinking_status",
            "body": "Found relevant context. Structuring answer..."
        }

        for part in agent.generate_content(
            prompt=prompt, 
            model_name=ModelName.GeminiFlashLite, 
            stream=True,
        ):
            yield {
                "type": "answer",
                "body": part.text
            }

        prompt = f"""
            Based on this original question: "{question}"
            Generate 3 related but different follow-up questions that users might want to ask next.
            Return only the questions, do not return the number or order of the question.
        """

        response_generator = agent.generate_content_and_normalize_results([prompt], model_name=ModelName.GeminiFlashLite)

        async for answer in response_generator:
            yield {
                "type": "related_question",
                "body": answer
            }
    except Exception as e:
        yield {
            "type": "answer",
            "body": f"❌ Error generating answer."
        }

async def handle_company_specific_finance(ticker, question):
    ticker = ticker.lower().strip()

    # Get company fundamental data
    company_fundamental = get_company_fundamental(ticker)

    # Search for relevant context from 10-K documents
    yield {
        "type": "thinking_status",
        "body": "Searching for relevant information from 10K document..."
    }

    openai_agent = Agent(model_type="openai")
    context_from_official_document = search_similar_content_and_format_to_texts(
        query_embeddings=openai_agent.generate_embedding(question),
        index_name=COMPANY_DOCUMENT_INDEX_NAME,
        filter={"ticker": ticker}
    )
    
    # Format search results into financial context
    financial_context = ""
    if context_from_official_document:
        financial_context = f"\nRelevant information from company's 10K documents:\n\n{context_from_official_document}"
    
    try:
        annual_financial_statements = [
            CompanyFinancialConnector.to_dict(item) for item in company_financial_connector.get_company_financial_statements(ticker)
        ]
        quarterly_financial_statements = [
            CompanyFinancialConnector.to_dict(item) for item in company_financial_connector.get_company_quarterly_financial_statements(ticker)
        ]

        financial_context += f"""
            You are a financial expert who can give in-depth answer for company finance related questions based on financial data.
            Here is the question: {question}.
            Here are the financial statements and company fundamental data for {ticker.upper()}:
                Company Fundamental Data:
                {company_fundamental}
                Company Financial Statements:
                Annual Financial Statements:
                {annual_financial_statements}
                Quarterly Financial Statements:
                {quarterly_financial_statements}

            To answer the question, analyse the data with these guidelines:
            1. Use specific numbers from the statements. Use billions or millions as appropriate.
            2. Calculate year-over-year changes when relevant
            3. Present growth rates as percentages
            4. Ensure numerical consistency across years
            Combine the analysis with relevant news and trends of the company to provide a comprehensive answer.

            At the beginning of the analysis, have a summary of 50-100 words of the analysis.
            Then have a follow-up section of 150-200 words in total with more in-depth analysis.
            At the end of the analysis, state clear which source you get the information from.
        """
        for part in agent.generate_content([
            financial_context,
            analysis_prompt,
        ], model_name=ModelName.GeminiFlash, stream=True, thought=True):
            if part.thought:
                yield {
                    "type": "thinking_status",
                    "body": part.text
                }
            else:
                yield {
                    "type": "answer",
                    "body": part.text if part.text else "❌ No analysis generated from the model"
                }

        prompt = f"""
            Based on this original question: "{question}"
            Generate 3 related but different follow-up questions that users might want to ask next.
            These questions should be related to either balance sheet, income statement or cash flow statement.
            Return only the questions, do not return the number or order of the question.
        """

        response = agent.generate_content_and_normalize_results([prompt], model_name=ModelName.GeminiFlashLite)
        async for answer in response:
            yield {
                "type": "related_question",
                "body": answer
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
        QuestionType.COMPANY_GENERAL.value: lambda: handle_company_general_question(ticker, question),
        QuestionType.COMPANY_SPECIFIC_FINANCE.value: lambda: handle_company_specific_finance(ticker, question)
    }

    handler = handlers.get(classification)
    if handler:
        async for chunk in handler():
            yield chunk
    else:
        yield "❌ Unable to classify question type"
