from logging import getLogger

from agent.agent import Agent
from ai_models.model_name import ModelName
from connectors.company import CompanyConnector

company_connector = CompanyConnector()

logger = getLogger(__name__)

agent = Agent(model_type="gemini", model_name=ModelName.Gemini25FlashLite)

DEFAULT_QUESTIONS = [
    "What is the company's revenue?",
    "What is the company's net income?",
    "What is the company's cash flow?",
]


async def get_general_frequent_ask_questions():
    try:
        questions_generator = agent.generate_content_and_normalize_results(
            [
                "Generate 3 questions that customers would ask about a particular financial concept such as revenue, net income, cash flow, etc.",
                "The question should be generic and not specific to any particular company.",
                "The questions should be concise and to the point.",
                "The questions should be in the form of a list of questions. Only return the list of questions without the number at the beginning, no other text.",
            ],
            model_name=ModelName.Gemini25FlashLite,
        )

        async for question in questions_generator:
            if question.strip():
                yield {"type": "question", "text": question.strip()}

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
        # Generate questions (streaming)
        response_generator = agent.generate_content_and_normalize_results(
            [
                f"The company name is {company.name}. Their ticker name is {ticker}",
                "Generate 3 questions that users would ask about this company.",
                "1 question is about the company's general information such as who founded the company, when it was founded, etc.",
                "1 question is about the company's products, services, business model, competitive advantage, etc.",
                "1 question is about the company's financial statements such as balance sheet, income statement, cash flow statement, etc in the LATEST financial year.",
                "Only return the content of the questions. Do not return the number or order of the output.",
            ],
            model_name=ModelName.Gemini25FlashLite,
        )

        async for question in response_generator:
            yield {"type": "question", "text": question}
    except Exception as e:
        logger.error(f"Error generating frequent ask questions for {ticker}: {e}")
        # After error, yield default questions
        for question in DEFAULT_QUESTIONS:
            yield {"type": "question", "text": question}
