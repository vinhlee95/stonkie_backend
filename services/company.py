from external_knowledge.company_fundamental import get_company_fundamental
from pydantic import BaseModel
import logging
import re
import json
import time
from agent.agent import Agent
from connectors.database import SessionLocal, Base, engine
from connectors.pdf_reader import get_pdf_content_from_bytes, PageData
from models.company_financial import CompanyFinancials
import os
from pinecone import Pinecone

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

def chunk_text(pages_data: list[PageData], chunk_size: int = 1000, overlap: int = 100) -> list[dict]:
    """Split text into overlapping chunks while preserving page numbers.
    
    Args:
        pages_data (list[dict]): List of dictionaries containing page number and text
        chunk_size (int): Size of each chunk in characters
        overlap (int): Number of characters to overlap between chunks
        
    Returns:
        list[dict]: List of chunks with page numbers
    """
    chunks = []
    current_chunk = ""
    current_page = 0
    
    for page_data in pages_data:
        text = page_data.text
        page_num = page_data.page_number
        
        # If adding this page would exceed chunk size, save current chunk and start new one
        if len(current_chunk) + len(text) > chunk_size and current_chunk:
            chunks.append({
                "text": current_chunk.strip(),
                "pages": page_num
            })
            # Start new chunk with overlap
            current_chunk = current_chunk[-overlap:] if overlap > 0 else ""
            current_page += 1
            
        current_chunk += text + " "
        current_page += 1
    
    # Add the last chunk if it exists
    if current_chunk:
        chunks.append({
            "text": current_chunk.strip(),
            "pages": current_page
        })
    
    return chunks

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

async def handle_10k_file(file_content: bytes, ticker: str, year: int) -> dict:
    """Process 10-K PDF file content and save financial data
    
    Args:
        file_content (bytes): PDF file content
        ticker (str): Company ticker symbol
        year (int): Year of the 10-K report
    
    Returns:
        dict: Saved financial data record
    """
    try:    
        start_time = time.time()
        
        # Extract PDF content from bytes with page numbers
        pdf_content_start = time.time()
        pages_data = get_pdf_content_from_bytes(file_content)
        pdf_content_end = time.time()
        logger.info(f"PDF content extraction took {pdf_content_end - pdf_content_start:.2f} seconds")
        
        # Process PDF content into chunks with page tracking
        chunk_start = time.time()
        chunks = chunk_text(pages_data)
        chunk_end = time.time()
        logger.info(f"Text chunking took {chunk_end - chunk_start:.2f} seconds for {len(chunks)} chunks")
        
        # Generate embeddings and store in Pinecone
        embedding_start = time.time()
        vectors = []
        agent = Agent(model_type="openai")
        for i, chunk in enumerate(chunks):
            embedding = agent.generate_embedding(input=chunk["text"])
            vectors.append({
                'id': f"{ticker}-chunk-{i}",
                'values': embedding,
                'metadata': {
                    'ticker': ticker,
                    'year': year,
                    'chunk_index': i,
                    'text': chunk["text"],
                    'page_number': chunk["pages"]
                }
            })
        embedding_end = time.time()
        logger.info(f"Embedding took {embedding_end - embedding_start:.2f} seconds for {len(vectors)} vectors")
        
        # Initialize Pinecone
        index = init_pinecone()
        pinecone_start = time.time()
        batch_size = 100
        for i in range(0, len(vectors), batch_size):
            batch = vectors[i:i + batch_size]
            index.upsert(vectors=batch)
        pinecone_end = time.time()
        logger.info(f"Uploading to Pinecone took {pinecone_end - pinecone_start:.2f} seconds for {len(vectors)} vectors")
        
        # Get analysis from AI agent
        analysis_start = time.time()
        analysis = analyze_10k_revenue("\n".join([chunk["text"] for chunk in chunks]))
        analysis_end = time.time()
        logger.info(f"AI analysis took {analysis_end - analysis_start:.2f} seconds")
        
        # Save to database
        save_start = time.time()
        saved_data = save_analysis(ticker.upper(), analysis, "\n".join([chunk["text"] for chunk in chunks]))
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

def init_pinecone():
    """Initialize Pinecone client."""
    pc = Pinecone(api_key=os.getenv('PINECONE_API_KEY'))
    index = pc.Index("company10k")
    return index
