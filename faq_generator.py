import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

DEFAULT_QUESTIONS = [
    "What is the company's revenue?",
    "What is the company's net income?",
    "What is the company's cash flow?"
]

def get_general_frequent_ask_questions():
    try:
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction="""
            You are a professional financial analyst who specializes in anticipating questions from customers.
            """
        )
        
        response = model.generate_content([
            "Generate 3 questions that customers would ask about a particular financial concept such as revenue, net income, cash flow, etc.",
            "The question should be generic and not specific to any particular company.",
            "The questions should be concise and to the point.",
            "The questions should be in the form of a list of questions. Only return the list of questions without the number at the beginning, no other text.",
        ])
        
        # Remove "*" symbols and strip whitespace from each question
        return [q.replace("*", "").strip() for q in response.text.split("\n") if q.strip()]
    except Exception as e:
        # Return placeholder questions
        return DEFAULT_QUESTIONS


def get_frequent_ask_questions_for_ticker(ticker):
    """
    Get 3 frequent ask questions for a given ticker symbol
    """
    try:
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction="""
            You are a professional financial analyst who specializes in anticipating questions from customers.
            """
        )

        company_name = model.generate_content(f"What is the company's name in full instead of ticker symbol for {ticker}?")
        
        response = model.generate_content([
            f"Here is the company's name: {company_name.text}",
            "Generate 3 questions that customers would ask about this ticker symbol.",
            "We only have balance sheet, income statement, and cash flow statements for this company.",
            "The questions should be related to one of the company's financial statements.",
            "The questions should be concise and to the point.",
            "The questions should be in the form of a list of questions.",
        ])
      
        # Remove "*" symbols and strip whitespace from each question
        return [q.replace("*", "").strip() for q in response.text.split("\n") if q.strip()]
    except Exception as e:
        # Return placeholder questions
        return DEFAULT_QUESTIONS

async def get_frequent_ask_questions_for_ticker_stream(ticker):
    """
    Streaming version: Get 3 frequent ask questions for a given ticker symbol
    Yields questions as they are generated
    """

    yield {"type": "status", "message": "Here are some frequently asked questions about this ticker symbol:"}

    try:
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            generation_config=genai.GenerationConfig(
                temperature=0.1,
                max_output_tokens=150,
            )
        )
        
        # Generate questions (streaming)
        response = model.generate_content(
            [
                f"Here is the company's ticker name: {ticker}",
                "Generate 3 questions that customers would ask about this ticker symbol.",
                "We only have balance sheet, income statement, and cash flow statements for this company.",
                "The questions should be related to one of the company's financial statements.",
                "The questions should be concise and to the point.",
                "The questions should be in the form of a list of questions.",
            ],
            stream=True
        )

        current_question = ""
        question_number = 1
        
        for chunk in response:
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
                                "type": "question",
                                "number": question_number,
                                "text": clean_question
                            }
                            question_number += 1
                    # Keep the incomplete part
                    current_question = parts[-1]
        
        # Handle the last question if there is one
        if current_question.strip():
            clean_question = current_question.replace("*", "").strip()
            yield {
                "type": "question",
                "number": question_number,
                "text": clean_question
            }
            
        # Yield completion status
        yield {"type": "status", "message": "completed"}
            
    except Exception as e:
        yield {"type": "error", "message": str(e)}
        # After error, yield default questions
        for i, question in enumerate(DEFAULT_QUESTIONS, 1):
            yield {
                "type": "question",
                "number": i,
                "text": question
            }
