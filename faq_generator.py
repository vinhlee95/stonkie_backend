from logging import getLogger

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from connectors.company import CompanyConnector
from services.etf import get_etf_by_ticker

company_connector = CompanyConnector()

logger = getLogger(__name__)

DEFAULT_QUESTIONS = [
    "What is the company's revenue?",
    "What is the company's net income?",
    "What is the company's cash flow?",
]


async def get_general_frequent_ask_questions():
    try:
        agent = MultiAgent(model_name=ModelName.Gemini25FlashLite)

        prompt = """
            Generate exactly 3 questions that customers would ask about a particular financial concept such as revenue, net income, cash flow, etc.

            Requirements:
            - The questions should be generic and not specific to any particular company
            - The questions should be concise and to the point
            - Put EACH question on its OWN LINE
            - Do NOT number the questions or add any prefixes
        """

        for question in agent.generate_content_by_lines(
            prompt=prompt,
            use_google_search=False,
            max_lines=3,
            min_line_length=10,
            strip_numbering=True,
            strip_markdown=True,
        ):
            yield {"type": "question", "text": question}

    except Exception as e:
        logger.error(f"Error generating general frequent ask questions: {e}")
        # Return placeholder questions
        for question in DEFAULT_QUESTIONS:
            yield {"type": "question", "text": question}


async def get_frequent_ask_questions_for_ticker_stream(ticker):
    """
    Streaming version: Get 3 frequent ask questions for a given ticker symbol
    Yields questions as they are generated
    Supports both companies and ETFs
    """
    # Check if ticker is an ETF
    etf_data = get_etf_by_ticker(ticker)

    if etf_data:
        # Generate ETF-specific FAQs
        yield {
            "type": "status",
            "message": f"Here are some frequently asked questions about {etf_data.name} ({ticker})",
        }

        try:
            agent = MultiAgent(model_name=ModelName.Gemini25FlashLite)

            prompt = f"""
                The ETF name is {etf_data.name}. Their ticker is {ticker}. Index tracked: {etf_data.index_tracked or 'N/A'}.

                Generate exactly 4 questions that investors would ask about this ETF:
                - 1 question about the ETF's costs (TER, tracking difference, fees)
                - 1 question about the ETF's holdings or sector allocation
                - 1 question about the ETF's structure (replication method, fund provider, domicile)
                - 1 question comparing this ETF to similar alternatives

                Requirements:
                - Put EACH question on its OWN LINE
                - Do NOT number the questions or add any prefixes
                - Keep questions concise and natural (8-15 words)
                - Use proper ETF terminology
            """

            for question in agent.generate_content_by_lines(
                prompt=prompt,
                use_google_search=False,
                max_lines=4,
                min_line_length=10,
                strip_numbering=True,
                strip_markdown=True,
            ):
                yield {"type": "question", "text": question}

        except Exception as e:
            logger.error(f"Error generating ETF frequent ask questions for {ticker}: {e}")
            # Fallback ETF questions
            yield {"type": "question", "text": f"What is the TER of {ticker}?"}
            yield {"type": "question", "text": f"What are the top holdings in {ticker}?"}
            yield {"type": "question", "text": f"What index does {ticker} track?"}
            yield {"type": "question", "text": f"How is {ticker} replicated?"}

    else:
        # Generate company-specific FAQs
        company = company_connector.get_by_ticker(ticker)
        yield {
            "type": "status",
            "message": f"Here are some frequently asked questions about {company.name} ({company.ticker})",
        }

        try:
            agent = MultiAgent(model_name=ModelName.Gemini25FlashLite)

            prompt = f"""
                The company name is {company.name}. Their ticker name is {ticker}.

                Generate exactly 2 questions that users would ask about this company:
                - 1 question about the company's general information such as who founded the company, when it was founded, etc.
                - 1 question about the company's products, services, business model, competitive advantage, etc.

                Requirements:
                - Put EACH question on its OWN LINE
                - Do NOT number the questions or add any prefixes
                - Keep questions concise and natural
            """

            for question in agent.generate_content_by_lines(
                prompt=prompt,
                use_google_search=False,
                max_lines=2,
                min_line_length=10,
                strip_numbering=True,
                strip_markdown=True,
            ):
                yield {"type": "question", "text": question}

            # Hard-coded questions about latest highlights from quarterly report
            yield {
                "type": "question",
                "text": f"What are the key highlights from {company.name}'s latest quarterly report?",
            }

            # Hard-coded questions about latest highlights from annual report
            yield {
                "type": "question",
                "text": f"What are the key highlights from {company.name}'s latest annual report?",
            }
        except Exception as e:
            logger.error(f"Error generating frequent ask questions for {ticker}: {e}")
            # After error, yield default questions
            for question in DEFAULT_QUESTIONS:
                yield {"type": "question", "text": question}
