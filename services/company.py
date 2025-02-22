from itertools import chain
from typing import Literal
from external_knowledge.company_fundamental import get_company_fundamental
from pydantic import BaseModel
import logging
import re
import json
import time
from agent.agent import Agent
from pypdf import PdfReader
from connectors.database import SessionLocal, Base, engine
from models.company_financial import CompanyFinancials

class CompanyFundamental(BaseModel):
    market_cap: int
    pe_ratio: float
    revenue: int
    net_income: int
    basic_eps: float
    sector: str
    industry: str
    description: str
    country: str
    exchange: str
    dividend_yield: float

def get_key_stats_for_ticker(ticker: str):
    """
    Get key stats for a given ticker symbol
    """
    company_fundamental = get_company_fundamental(ticker)
    
    # Convert MarketCapitalization to int, handle None/None values for dividend yield
    market_cap = int(company_fundamental["MarketCapitalization"]) if company_fundamental["MarketCapitalization"] != "None" else 0
    dividend_yield = float(company_fundamental["DividendYield"]) if company_fundamental["DividendYield"] != "None" else 0.0
    
    return CompanyFundamental(
        market_cap=market_cap,
        pe_ratio=float(company_fundamental["PERatio"]),
        revenue=int(company_fundamental["RevenueTTM"]),
        net_income=int(float(company_fundamental["EPS"]) * float(company_fundamental["SharesOutstanding"])),
        basic_eps=float(company_fundamental["EPS"]),
        sector=company_fundamental["Sector"],
        industry=company_fundamental["Industry"],
        description=company_fundamental["Description"],
        country=company_fundamental["Country"],
        exchange=company_fundamental["Exchange"],
        dividend_yield=dividend_yield
    )


# Create tables
Base.metadata.create_all(bind=engine)

logger = logging.getLogger(__name__)

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
        - revenue: number. Numbers should be in thousands. Do not include any delimiters.
        - percentage: number
    - breakdown: list of objects, if the type is "region", each containing the following fields:
        - region: string
        - revenue: number. Numbers should be in thousands. Do not include any delimiters.
        - percentage: number
    
    Pay close attention to the revenue number. If the report says that the revenue is in millions, convert it to thousands.
    
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

async def handle_10k_file(file_content: bytes, ticker: str, year: int) -> dict:
    """Process 10-K PDF file content and save financial data
    
    Args:
        file_content (bytes): PDF file content
        ticker (str): Company ticker symbol
    
    Returns:
        dict: Saved financial data record
    """
    try:
        start_time = time.time()
        
        # Extract PDF content from bytes
        pdf_content_start = time.time()
        pdf_content = get_pdf_content_from_bytes(file_content)
        pdf_content_end = time.time()
        logger.info(f"PDF content extraction took {pdf_content_end - pdf_content_start:.2f} seconds")
        
        # Get analysis from AI agent
        analysis_start = time.time()
        analysis = analyze_10k_revenue(pdf_content)
        analysis_end = time.time()
        logger.info(f"AI analysis took {analysis_end - analysis_start:.2f} seconds")
        
        # Save to database
        save_start = time.time()
        
        saved_data = save_analysis(ticker.upper(), year, analysis, pdf_content)
        save_end = time.time()
        logger.info(f"Saving to database took {save_end - save_start:.2f} seconds")
        
        total_time = time.time() - start_time
        logger.info(f"Total execution time: {total_time:.2f} seconds")
        
        return {
            "id": saved_data.id,
            "company_symbol": saved_data.company_symbol,
            "year": saved_data.year,
            "revenue_breakdown": saved_data.revenue_breakdown
        }
        
    except Exception as e:
        logger.error(f"Error processing 10-K file: {str(e)}")
        raise

def get_pdf_content_from_bytes(file_content: bytes) -> str:
    """Extract text content from PDF bytes"""
    from io import BytesIO
    reader = PdfReader(BytesIO(file_content))
    text_content = ""
    for page in reader.pages:
        text_content += page.extract_text()
    return text_content

class ProductRevenueBreakdown(BaseModel):
    product: str
    revenue: int
    percentage: float

class RegionRevenueBreakdown(BaseModel):
    region: str
    revenue: int
    percentage: float

class RevenueBreakdown(BaseModel):
    type: Literal["product"]
    breakdown: list[ProductRevenueBreakdown]

class RegionBreakdown(BaseModel):
    type: Literal["region"]
    breakdown: list[RegionRevenueBreakdown]


class RevenueBreakdownDTO(BaseModel):
    year: int
    revenue_breakdown: list[RevenueBreakdown | RegionBreakdown]

class NewRevenueBreakdownDTO(BaseModel):
    year: int
    product_breakdown: list[ProductRevenueBreakdown]
    region_breakdown: list[RegionRevenueBreakdown]

def get_revenue_breakdown_for_company(ticker: str) -> list[NewRevenueBreakdownDTO] | None:
    """Get revenue breakdown for a given company"""
    db = SessionLocal()
    try:
        financial_data = db.query(CompanyFinancials).filter(CompanyFinancials.company_symbol == ticker.upper()).order_by(CompanyFinancials.year.desc())
        if financial_data.count() == 0:
            return None

        revenue_breakdown: list[NewRevenueBreakdownDTO] = []

        for data in financial_data.all():
            year = data.year
            product_breakdown = list(chain.from_iterable([item.get('breakdown') for item in data.revenue_breakdown if item.get('type') == "product"]))
            region_breakdown = list(chain.from_iterable([item.get('breakdown') for item in data.revenue_breakdown if item.get('type') == "region"]))
            
            revenue_breakdown.append(NewRevenueBreakdownDTO(
                year=year,
                product_breakdown=[ProductRevenueBreakdown(**item) for item in product_breakdown],
                region_breakdown=[RegionRevenueBreakdown(**item) for item in region_breakdown]
            ))
        
        return revenue_breakdown
    except Exception as e:
        logger.error(f"Error getting revenue breakdown for company", {
            "ticker": ticker,
            "error": str(e)
        })
        return None
