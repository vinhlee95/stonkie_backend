from pathlib import Path
import sys
from agent.agent import Agent
from pypdf import PdfReader
import json
import time
import re

from connectors.database import SessionLocal, Base, engine
from models.company_financial import CompanyFinancials

# Create tables
Base.metadata.create_all(bind=engine)

def get_pdf_content(pdf_path):
    """Extract text content from PDF file"""
    reader = PdfReader(pdf_path)
    text_content = ""
    for page in reader.pages:
        text_content += page.extract_text()
    return text_content

def analyze_10k_revenue(content):
    """Use AI agent to analyze revenue breakdown from 10-K"""
    agent = Agent(model_type="gemini")
    
    prompt = """
    Analyze the following 10-K document content and provide revenue stream breakdown
    by product, services and regions, with percentage breakdown.

    Return the response in JSON format.
    The JSON should be a list of objects, each containing the following fields:
    - type: string, either "product" or "region"
    - breakdown: list of objects, if the type is "product", each containing the following fields:
        - product: string
        - revenue: number
        - percentage: number
    - breakdown: list of objects, if the type is "region", each containing the following fields:
        - region: string
        - revenue: number
        - percentage: number
    
    
    Document content:
    {content}
    """
    
    response = agent.generate_content(
        prompt.format(content=content), 
        stream=False,
    )

    return response.text

def save_analysis(company_symbol: str, year: int, analysis_result: str, raw_text: str):
    """Save analysis results to database"""
    db = SessionLocal()
    try:
        # Remove the Markdown code block markers and parse the JSON
        json_match = re.search(r'```json\n(.*)\n```', analysis_result, re.DOTALL)
        if json_match:
            json_content = json_match.group(1)
            revenue_data = json.loads(json_content)
        else:
            raise ValueError("No JSON content found in the analysis result")

        financial_data = CompanyFinancials(
            company_symbol=company_symbol,
            year=year,
            revenue_breakdown=revenue_data
        )
        db.add(financial_data)
        db.commit()
        db.refresh(financial_data)
        return financial_data
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()

def main():
    # Get ticker symbol from user
    ticker = input("Enter stock ticker symbol (e.g., TSLA, AAPL): ").strip()

    if not ticker:
        print("Error: Ticker symbol is required")
        sys.exit(1)
    
    # Get the current script's directory
    script_dir = Path(__file__).parent
    pdf_path = script_dir / f"2024_{ticker.lower()}_10k.pdf"
    
    try:
        start_time = time.time()
        
        # Extract PDF content
        pdf_content_start = time.time()
        pdf_content = get_pdf_content(pdf_path)
        pdf_content_end = time.time()
        print(f"PDF content extraction took {pdf_content_end - pdf_content_start:.2f} seconds")
        
        # Get analysis from AI agent
        analysis_start = time.time()
        analysis = analyze_10k_revenue(pdf_content)
        analysis_end = time.time()
        print(f"AI analysis took {analysis_end - analysis_start:.2f} seconds")
        
        # Save to database
        save_start = time.time()

        # TODO: extract year from the 10-K
        year = int(pdf_path.stem.split('_')[0])  # Extract year from pdf filename
        
        saved_data = save_analysis(ticker.upper(), year, analysis, pdf_content)
        save_end = time.time()
        print(f"Saving to database took {save_end - save_start:.2f} seconds")
        
        total_time = time.time() - start_time
        print(f"Total execution time: {total_time:.2f} seconds")
        
        print("\nData saved to database with ID:", saved_data.id)
    except FileNotFoundError:
        print(f"Error: Could not find PDF file at {pdf_path}")
    except Exception as e:
        print(f"An error occurred: {str(e)}")

if __name__ == "__main__":
    main()
