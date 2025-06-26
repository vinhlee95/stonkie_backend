import logging
import re
import json
import time
from agent.agent import Agent
from connectors.database import SessionLocal, Base, engine
from connectors.pdf_reader import get_pdf_content_from_bytes, PageData
from connectors.vector_store import search_similar_content_and_format_to_texts
from models.company_financial import CompanyFinancials
from connectors.vector_store import init_vector_record, add_vector_record_by_batch
from connectors.company import CompanyConnector, CompanyFundamentalDto, get_all
from connectors.company_financial import CompanyFinancialConnector

COMPANY_DOCUMENT_INDEX_NAME = "company10k"

company_financial_connector = CompanyFinancialConnector()
company_connector = CompanyConnector()
    
def get_key_stats_for_ticker(ticker: str) -> CompanyFundamentalDto | None:
    return company_connector.get_fundamental_data(ticker=ticker)

async def get_swot_analysis_for_ticker(ticker: str):
    # Get relevant info from 10K file
    embedding_agent = Agent(model_type="openai")
    swot_prompt = f"""strengths, weaknesses, opportunities, and threats of {ticker.upper()}?"""
    relevant_info_from_10k = search_similar_content_and_format_to_texts(
        query_embeddings=embedding_agent.generate_embedding(swot_prompt),
        index_name=COMPANY_DOCUMENT_INDEX_NAME,
        filter={"ticker": ticker.lower()},
        top_k=20
    )

    agent = Agent(model_type="gemini")
    prompt = f"""
        Generate a SWOT analysis for company {ticker.upper()}.
        Here are relevant information from 10-K file:
        {relevant_info_from_10k}.
        Use your general knowledge to supplement the insights from the 10K file. 
        Return the response in JSON format. The response is an object with following fields:
        - strength: list of string
        - weakness: list of string
        - opportunity: list of string
        - threat: list of string

        Each list should have maximum 3 items. Keep the words amount in each SWOT aspect within 100 words.
        At the end of each list, say explicitly where it the source of your analysis.
    """
    accumulated_text = ""
    response = await agent.generate_content(
        prompt=prompt,
        stream=True
    )

    async for chunk in response:
        accumulated_text += chunk.text
        
    # Parse JSON from accumulated_text
    json_match = re.search(r'```json\s*(.+?)\s*```', accumulated_text, re.DOTALL)
    if json_match:
        json_content = json_match.group(1)
        return json.loads(json_content)
    else:
        logger.error("Failed to extract JSON from response", {
            "ticker": ticker,
            "response": accumulated_text
        })
        return None

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
    
    # Use non-streaming response for simplicity
    response_obj = agent.generate_content(
        prompt.format(content=content), 
        stream=False,
    )
    
    # For non-streaming responses, we can directly access the text property
    if hasattr(response_obj, 'text'):
        return response_obj.text
    else:
        # Handle case where response might be a different type
        return str(response_obj)

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

def init_vector_record_for_company(ticker: str, year: int, text: str, page_number: int, chunk_index: int):
    agent = Agent(model_type="openai")
    return init_vector_record(
        id=f"{ticker}-chunk-{chunk_index}",
        embeddings=agent.generate_embedding(text),
        metadata={
            "ticker": ticker,
            "year": year,
            "chunk_index": chunk_index,
            "text": text,
            "page_number": page_number,
        }
    )

async def handle_company_report(file_content: bytes, ticker: str, year: int, extract_revenue: bool = False, extract_insights: bool = False) -> dict:
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
        
        if extract_insights:
            # Generate embeddings and store in Pinecone
            logger.info(f"Generate embeddings for {len(chunks)} chunks")
            embedding_start = time.time()
            vectors = []
            for i, chunk in enumerate(chunks):
                vectors.append(init_vector_record_for_company(ticker, year, chunk["text"], chunk["pages"], i))
            embedding_end = time.time()
            logger.info(f"Embedding took {embedding_end - embedding_start:.2f} seconds for {len(vectors)} vectors")
            
            add_vector_record_by_batch(COMPANY_DOCUMENT_INDEX_NAME, vectors)
        
        if extract_revenue:
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
            "ticker": ticker,
            "year": year,
        }
        
    except Exception as e:
        logger.error(f"Error processing 10-K file: {str(e)}")
        raise

def get_all_companies():
    companies_having_financial_data = company_financial_connector.get_company_tickers_having_financial_data()
    all_companies = get_all()
    return [company for company in all_companies if company.ticker in companies_having_financial_data]


def get_company_financial_statements(ticker: str, report_type: str | None = None, period_type: str | None = None):
    try:
        # Get statements based on period type
        statements = (
            company_financial_connector.get_company_quarterly_financial_statements(ticker)
            if period_type == "quarterly"
            else company_financial_connector.get_company_financial_statements(ticker)
        )
        
        if not report_type:
            return statements
            
        # Map report types to their corresponding fields
        report_type_to_field = {
            "balance_sheet": "balance_sheet",
            "cash_flow": "cash_flow",
            "income_statement": "income_statement"
        }
        
        # Filter and transform statements based on report type
        filtered_statements = []
        for statement in statements:
            data_field = getattr(statement, report_type_to_field[report_type])
            if data_field is None:
                continue
                
            # Create statement data based on period type
            statement_data = {
                "data": data_field
            }
            
            if period_type == "quarterly":
                statement_data["period_end_quarter"] = statement.period_end_quarter
            else:
                statement_data.update({
                    "period_end_year": statement.period_end_year,
                    "is_ttm": statement.is_ttm
                })
            
            filtered_statements.append(statement_data)
            
        return filtered_statements
        
    except Exception as e:
        logger.error(f"Error getting company financial statements: {str(e)}")
        return None
