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
            model_name="gemini-1.5-pro",
            system_instruction="""
            You are a professional financial analyst who specializes in anticipating questions from customers.
            """
        )
        
        response = model.generate_content([
            "Generate 3 questions that customers would ask about a particular financial concept such as revenue, net income, cash flow, etc.",
            "The question should be generic and not specific to any particular company.",
            "The questions should be concise and to the point.",
            "The questions should be in the form of a list of questions. Only return the list of questions, no other text.",
        ])
        
        return [q for q in response.text.split("\n") if q.strip()]
    except Exception as e:
        # Return placeholder questions
        return DEFAULT_QUESTIONS


def get_frequent_ask_questions_for_ticker(ticker):
    """
    Get 3 frequent ask questions for a given ticker symbol
    """
    try:
        model = genai.GenerativeModel(
            model_name="gemini-1.5-pro",
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
      
        return [q for q in response.text.split("\n") if q.strip()]
    except Exception as e:
        # Return placeholder questions
        return DEFAULT_QUESTIONS
