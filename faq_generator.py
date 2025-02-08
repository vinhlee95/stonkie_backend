from agent.agent import Agent
from logging import getLogger

logger = getLogger(__name__)

agent = Agent(model_type="gemini")

DEFAULT_QUESTIONS = [
    "What is the company's revenue?",
    "What is the company's net income?",
    "What is the company's cash flow?"
]

async def get_general_frequent_ask_questions():
    try:
        questions_generator = agent.generate_content_and_normalize_results([
            "Generate 3 questions that customers would ask about a particular financial concept such as revenue, net income, cash flow, etc.",
            "The question should be generic and not specific to any particular company.",
            "The questions should be concise and to the point.",
            "The questions should be in the form of a list of questions. Only return the list of questions without the number at the beginning, no other text.",
        ])
        
        async for question in questions_generator:
            if question.strip():
                yield {
                    "type": "question",
                    "text": question.strip()
                }
            
    except Exception as e:
        logger.error(f"Error generating general frequent ask questions: {e}")
        # Return placeholder questions
        for question in DEFAULT_QUESTIONS:
            yield {
                "type": "question",
                "text": question
            }
            

async def get_frequent_ask_questions_for_ticker_stream(ticker):
    """
    Streaming version: Get 3 frequent ask questions for a given ticker symbol
    Yields questions as they are generated
    """

    yield {"type": "status", "message": "Here are some frequently asked questions about this ticker symbol:"}

    try:
        # Generate questions (streaming)
        response_generator = agent.generate_content_and_normalize_results(
            [
                f"Here is the company's ticker name: {ticker}",
                "Generate 3 questions that customers would ask about this ticker symbol.",
                "1 question is about the company's general information such as who founded the company, when it was founded, etc.",
                "1 question is about the company's products, services, business model, competitive advantage, etc.",
                """
                    1 question is about the company's financial statements such as balance sheet, income statement, cash flow statement, etc.
                    The question should be about a financial year instead of quarterly. Do not return a variable year. Prefer the latest year.
                    Only return the content of the questions. Do not return the number or order of the output.
                """,
            ]
        )

        async for question in response_generator:
            yield {
                "type": "question",
                "text": question
            }
    except Exception as e:
        logger.error(f"Error generating frequent ask questions for {ticker}: {e}")
        # After error, yield default questions
        for question in DEFAULT_QUESTIONS:
            yield {
                "type": "question",
                "text": question
            }
