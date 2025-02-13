from pathlib import Path
from agent.agent import Agent
from pypdf import PdfReader

# Configure Gemini API

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
    
    response = agent.generate_content(prompt.format(content=content), stream=False)
    return response.text

def main():
    # Get the current script's directory
    script_dir = Path(__file__).parent
    pdf_path = script_dir / "2024_tsla_10k.pdf"
    
    try:
        # Extract PDF content
        pdf_content = get_pdf_content(pdf_path)
        
        # Get analysis from AI agent
        analysis = analyze_10k_revenue(pdf_content)
        
        print("\nKey Revenue Learnings from given 10-K:")
        print(analysis)
        
    except FileNotFoundError:
        print(f"Error: Could not find PDF file at {pdf_path}")
    except Exception as e:
        print(f"An error occurred: {str(e)}")

if __name__ == "__main__":
    main()
