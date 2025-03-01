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
    Try to find the data for all the years in the report.

    Return the response in JSON format.
    The JSON should be a list of objects, each containing the following fields:
    - year: number, the year in which the sales figure is recorded
    - type: string, either "product" or "region"
    - breakdown: list of objects, if the type is "product", each containing the following fields:
        - product: string
        - revenue: number. Numbers should be in thousands. Do not include any delimiters. If the report says that the revenue is in millions, convert it to thousands.
        - percentage: number
    - breakdown: list of objects, if the type is "region", each containing the following fields:
        - region: string
        - revenue: number. Numbers should be in thousands. Do not include any delimiters. If the report says that the revenue is in millions, convert it to thousands.
        - percentage: number
    
    If you cannot find the percentage in the report. Calculate the percentage on your own based on the revenue breakdown of each product or region.
    Do not include "total revenue" from the report to the output.
    
    Document content:
    {content}
    """
    
    response = agent.generate_content(
        prompt.format(content=content), 
        stream=False,
    )

    return response.text

def save_analysis(company_symbol: str, analysis_result: str, raw_text: str):
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

        # Transform the revenue data to have the correct format
        revenue_data_by_year = {}
        for item in revenue_data:
            year = item.get("year")
            if year not in revenue_data_by_year:
                revenue_data_by_year[year] = []
                revenue_data_by_year[year].append({
                    "type": item.get("type"),
                    "breakdown": item.get("breakdown")
                })
            else:
                revenue_data_by_year[year].append({
                    "type": item.get("type"),
                    "breakdown": item.get("breakdown")
                })
        
        # Get all existing data for this company in the years we're processing
        years = list(revenue_data_by_year.keys())
        existing_data = {
            (record.company_symbol, record.year): record
            for record in db.query(CompanyFinancials).filter(
                CompanyFinancials.company_symbol == company_symbol,
                CompanyFinancials.year.in_(years)
            ).all()
        }
        
        # Prepare batch insert for new records
        new_records = []
        saved_data = []
        
        for year, data in revenue_data_by_year.items():
            key = (company_symbol, year)
            if key in existing_data:
                logger.info(f"Data for {company_symbol} year {year} already exists, skipping...")
                saved_data.append(existing_data[key])
                continue
            
            new_record = CompanyFinancials(
                company_symbol=company_symbol,
                year=year,
                revenue_breakdown=data
            )
            new_records.append(new_record)
            saved_data.append(new_record)
        
        if new_records:
            db.bulk_save_objects(new_records)
            db.commit()

        return saved_data
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close()

async def handle_10k_file(file_content: bytes, ticker: str) -> dict:
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
        
        saved_data = save_analysis(ticker.upper(), analysis, pdf_content)
        save_end = time.time()
        logger.info(f"Saving to database took {save_end - save_start:.2f} seconds")
        
        total_time = time.time() - start_time
        logger.info(f"Total execution time: {total_time:.2f} seconds")
        
        return {
            "id": saved_data[0].id,
            "company_symbol": saved_data[0].company_symbol
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


def get_revenue_insights_for_company(ticker: str):
    # Fetch revenue data from DB
    db = SessionLocal()
    try:
        financial_data = db.query(CompanyFinancials).filter(CompanyFinancials.company_symbol == ticker.upper()).order_by(CompanyFinancials.year.desc()).all()
        if not financial_data:
            return None

        # Transform SQLAlchemy objects into dictionaries
        financial_data_list = []
        for data in financial_data:
            financial_data_list.append({
                'year': data.year,
                'revenue_breakdown': data.revenue_breakdown
            })

        agent = Agent(model_type="gemini")
        prompt = f"""
            You are a financial analyst tasked with analyzing revenue data for {ticker}. The data shows revenue breakdowns by product and region over multiple years.
            
            For each insight, apart from raw numbers taken from the data, provide an overview about the trend.
            based on your own knowledge about the product and services of that company. 
            For each product and region, explain why there are increasing or declining trend. Feel free to use general knowledge or news for this. Make sure that each insight has this analysis.

            For each data point:
            - Revenue numbers are in thousands of USD
            - Each year contains both product_breakdown and region_breakdown
            - Each breakdown item has revenue and percentage values

            Be specific and data-driven:
            - Use exact numbers and percentages
            - Reference specific years and time periods
            - Highlight significant changes with data points

            The first insight for each type (product or region) should be a general overview on all the income sources:
            - which product/region is the biggest source of revenue?
            - how big of a share does it account for?
            - is there a consistent growth/decline?
            - are there any shift in revenue mix?
            - is it dependent on specific products/regions?

            Here is the revenue data:
            {json.dumps(financial_data_list, indent=2)}

            Only return a list of insights in the output. Nothing else. 
            The JSON should be a list of objects, each containing the following fields:
            - type: string - either "product" or "region"
            - insight: string - the actual insight for that product or region
        """
        
        response = agent.generate_content(
            prompt=prompt,
            # TODO: add support for streaming
            stream=False,
        )

        # Remove the Markdown code block markers and parse the JSON
        json_match = re.search(r'```json\n(.*)\n```', response.text, re.DOTALL)
        if json_match:
            json_content = json_match.group(1)
            return json.loads(json_content)
        else:
            raise ValueError("No JSON content found in the analysis result")
    except Exception as e:
        logger.error(f"Error getting revenue insights for company", {
            "ticker": ticker,
            "error": str(e)
        })
        return None


async def get_revenue_insights_for_company_product(ticker: str):
    # Fetch revenue data from DB
    db = SessionLocal()
    try:
        financial_data = db.query(CompanyFinancials).filter(CompanyFinancials.company_symbol == ticker.upper()).order_by(CompanyFinancials.year.desc()).all()
        if not financial_data:
            yield {"type": "error", "content": "No revenue data found for that company"}
            return

        # Transform SQLAlchemy objects into dictionaries
        financial_data_list = []
        for data in financial_data:
            financial_data_list.append({
                'year': data.year,
                'revenue_breakdown': [item for item in data.revenue_breakdown if item.get("type") == "product"]
            })

        agent = Agent(model_type="gemini")
        prompt = f"""
            You are a financial analyst tasked with analyzing revenue data for {ticker}. The data shows revenue breakdowns by product over multiple years.
            Generate 5 insights for {ticker} based on the revenue data.
            
            For each insight, apart from raw numbers taken from the data, provide an overview about the trend
            based on your own knowledge about the product and services of that company. 
            For each product and service, explain why there are increasing or declining trend. Feel free to use general knowledge or news for this.
            Each insight has around 100 words.

            For each data point:
            - Revenue numbers are in thousands of USD. If the number is billion, just mention billion in the output instead of thousands.
            - Each breakdown item has revenue and percentage values

            Be specific and data-driven:
            - Use exact numbers and percentages
            - Reference specific years and time periods
            - Highlight significant changes with data points

            The first insight MUST be a general overview covering:
            - which product is the biggest source of revenue
            - how big of a share it accounts for
            - if there is consistent growth/decline
            - any shifts in revenue mix
            - no need to points out specific number or percentage in the first insight. Focus on general trend and observation.

            Here is the revenue data:
            {json.dumps(financial_data_list, indent=2)}

            Format your response as follows:
            1. Start each insight with "---INSIGHT_START---"
            2. End each insight with "---INSIGHT_END---"
            3. Make each insight self-contained and complete
            4. End the entire response with "---COMPLETE---"

            Generate insights one at a time, ensuring each is thorough and valuable.
            Do not include any other text or formatting outside of these markers.
        """
        
        response = await agent.generate_content(
            prompt=prompt,
            stream=True,
        )

        # Stream the response
        accumulated_text = ""
        current_insight = ""
        in_insight = False
        
        async for chunk in response:
            chunk_text = chunk.text
            accumulated_text += chunk_text
            
            # Process any complete insights
            while "---INSIGHT_START---" in accumulated_text and "---INSIGHT_END---" in accumulated_text:
                start_idx = accumulated_text.find("---INSIGHT_START---") + len("---INSIGHT_START---")
                end_idx = accumulated_text.find("---INSIGHT_END---")
                
                if start_idx > 0 and end_idx > start_idx:
                    insight_text = accumulated_text[start_idx:end_idx].strip()
                    yield {"type": "success", "data": {"content": insight_text}}
                    
                    # Remove processed insight from accumulated text
                    accumulated_text = accumulated_text[end_idx + len("---INSIGHT_END---"):]
                    current_insight = ""
                    in_insight = False
            
            # Handle streaming content between insights
            if "---INSIGHT_START---" in accumulated_text and not in_insight:
                in_insight = True
                start_idx = accumulated_text.rfind("---INSIGHT_START---") + len("---INSIGHT_START---")
                current_insight = accumulated_text[start_idx:]
            elif in_insight:
                current_insight += chunk_text
            
            # Stream current insight if it's meaningful and doesn't contain markers
            if current_insight.strip() and not any(marker in current_insight for marker in ["---INSIGHT_START---", "---INSIGHT_END---", "---COMPLETE---"]):
                yield {"type": "stream", "content": current_insight.strip()}
                current_insight = ""
            
            # Check if we're done
            if "---COMPLETE---" in accumulated_text:
                break

    except Exception as e:
        logger.error(f"Error getting revenue insights for company", {
            "ticker": ticker,
            "error": str(e)
        })
        yield {"type": "error", "content": str(e)}