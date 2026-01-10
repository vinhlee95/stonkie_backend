from logging import getLogger

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from connectors.company import CompanyConnector

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
    """
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
