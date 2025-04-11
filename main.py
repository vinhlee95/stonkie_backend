import base64
import json
import os
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
from typing import Dict
from google.oauth2 import service_account
import logging
from analyzer import analyze_financial_data_from_question
from enum import Enum
from services.company import get_key_stats_for_ticker, handle_company_report, get_swot_analysis_for_ticker, get_all_companies
from services.revenue_insight import get_revenue_insights_for_company_product, get_revenue_insights_for_company_region
from services.revenue_data import get_revenue_breakdown_for_company
from services.company import get_company_financial_statements
from faq_generator import get_general_frequent_ask_questions, get_frequent_ask_questions_for_ticker_stream
import time
from functools import lru_cache
from google.api_core import retry
from google.cloud.storage import Client
from fastapi.responses import StreamingResponse
from datetime import datetime

load_dotenv()

OUTPUT_DIR = "outputs"
BUCKET_NAME = "stock_agent_financial_report"

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Add logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    response = await call_next(request)
    logger.info(f"{request.method} {request.url.path} - {response.status_code}")
    return response

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://stonkie.netlify.app", "https://stonkie.vercel.app"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health check
@app.get("/api/health")
async def health_check():
    return {"status": "ok"}

class ReportType(Enum):
    INCOME_STATEMENT = "income_statement"
    BALANCE_SHEET = "balance_sheet"
    CASH_FLOW = "cash_flow"

# Cache the storage client initialization
@lru_cache(maxsize=1)
def get_storage_client():
    credentials = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
    if not credentials:
        print("❌ Google credentials not found in environment variables")
        return None
    
    credentials_dict = json.loads(base64.b64decode(credentials).decode('utf-8'))
    credentials = service_account.Credentials.from_service_account_info(credentials_dict)
    return Client(credentials=credentials)

# TODO: this is now cached forever in a lifetime of the process
# TODO: use Redis
# @lru_cache(maxsize=100)
def get_cached_financial_data(ticker: str, report_type: str) -> tuple:
    storage_client = get_storage_client()
    if not storage_client:
        return None, None
    
    # Configure retry with exponential backoff
    retry_config = retry.Retry(initial=1.0, maximum=60.0, multiplier=2.0)
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(f"{ticker.lower()}_{report_type}.csv")
    
    try:
        # Use retry for blob operations
        csv_content = blob.download_as_string(retry=retry_config)
        df = pd.read_csv(pd.io.common.BytesIO(csv_content))
        return df, df.columns.tolist()
    except Exception as e:
        logger.error(f"Error downloading blob: {str(e)}")
        return None, None

@app.get("/api/companies/{ticker}/statements")
def get_financial_statements(ticker: str):
    statements = get_company_financial_statements(ticker)
    return statements

@app.get("/api/financial-data/{ticker}/{report_type}")
async def get_financial_data(ticker: str, report_type: str) -> Dict:
    """
    Get financial data for a specific ticker and report type
    report_type can be: income_statement, balance_sheet, or cash_flow
    """
    start_time = time.time()
    try:
        # Validate and convert report_type to enum
        step_start = time.time()
        try:
            report_type_enum = ReportType(report_type)
        except ValueError:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid report type. Must be one of: {[rt.value for rt in ReportType]}"
            )
        logger.info(f"Report type validation took: {time.time() - step_start:.3f} seconds")

        # Get data from cache or download
        step_start = time.time()
        df, columns = get_cached_financial_data(ticker, report_type)
        if df is None:
            return {"data": [], "columns": []}
        logger.info(f"Data retrieval took: {time.time() - step_start:.3f} seconds")

        # Process data
        step_start = time.time()
        result = {
            "data": df.to_dict('records'),
            "columns": columns
        }
        logger.info(f"Data processing took: {time.time() - step_start:.3f} seconds")
        
        logger.info(f"Total execution time: {time.time() - start_time:.3f} seconds")
        return result
    
    except Exception as e:
        logger.error(f"Error occurred after {time.time() - start_time:.3f} seconds")
        raise HTTPException(status_code=500, detail=str(e))
    
@app.get("/api/companies/{ticker}/revenue")
async def get_revenue(ticker: str):
    """
    Get revenue for a given ticker symbol
    """
    revenue_breakdown = get_revenue_breakdown_for_company(ticker)
    return {"status": "success", "data": revenue_breakdown}

@app.get("/api/companies/{ticker}/revenue/insights/product")
async def get_revenue_insights_product(ticker: str):
    async def generate_insights():
        async for insight in get_revenue_insights_for_company_product(ticker):
            if insight.get("type") == "error":
                yield f"data: {json.dumps({'status': 'error', 'error': insight['content']})}\n\n"
                break
            elif insight.get("type") == "stream":
                yield f"data: {json.dumps({'status': 'streaming', 'content': insight['content']})}\n\n"
            else:
                yield f"data: {json.dumps({'status': 'success', 'data': insight['data']})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate_insights(),
        media_type="text/event-stream"
    )

@app.get("/api/companies/{ticker}/revenue/insights/region")
async def get_revenue_insights_region(ticker: str):
    async def generate_insights():
        async for insight in get_revenue_insights_for_company_region(ticker):
            if insight.get("type") == "error":
                yield f"data: {json.dumps({'status': 'error', 'error': insight['content']})}\n\n"
                break
            elif insight.get("type") == "stream":
                yield f"data: {json.dumps({'status': 'streaming', 'content': insight['content']})}\n\n"
            else:
                yield f"data: {json.dumps({'status': 'success', 'data': insight['data']})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generate_insights(),
        media_type="text/event-stream"
    )

@app.post("/api/company/analyze")
async def analyze_financial_data(request: Request):
    """
    Analyze financial statements for a given ticker symbol based on a specific question,
    streaming the results using Server-Sent Events
    
    Args:
        request (Request): FastAPI request object containing the question and ticker in body
    Returns:
        StreamingResponse: Server-sent events stream of analysis results
    """
    try:
        body = await request.json()
        question = body.get('question')
        ticker = body.get('ticker')
        
        if not question:
            raise HTTPException(status_code=400, detail="Question is required in request body")

        async def generate_analysis():
            async for chunk in analyze_financial_data_from_question(ticker, question):
                # Each chunk is now a JSON object with type and body
                yield json.dumps(chunk) + "\n\n"

        return StreamingResponse(
            generate_analysis(),
            media_type="text/event-stream"
        )
    except Exception as e:
        logger.error(f"Error during analysis: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Something went wrong. Please try again later.")


@app.get("/api/company/faq")
async def get_faq(request: Request):
    """
    Suggest 3 FAQs for a given ticker symbol
    """
    try:
        # Get ticker symbol from query params
        ticker = request.query_params.get('ticker')
        stream = request.query_params.get('stream')

        if not ticker:
            # Come up with 3 generic questions
            async def generate_stream():
                yield f"data: {json.dumps({
                    'type': 'status',
                    'message': 'Hi! My name is Stonkie. I can help you understand how a company is doing financially. Please pick a ticker symbol to get started.\n\n' + 
                              'I can also help with general finance questions. Here are some frequently asked questions about general financial concepts. Feel free to pick a question to see what I can do.'
                })}\n\n"

                async for item in get_general_frequent_ask_questions():
                    yield f"data: {json.dumps(item)}\n\n"

            return StreamingResponse(
                generate_stream(),
                media_type="text/event-stream"
            )
        
        # If stream parameter is provided and is "true", use streaming response
        if stream and stream.lower() == "true":
            async def generate_stream():
                async for item in get_frequent_ask_questions_for_ticker_stream(ticker):
                    yield f"data: {json.dumps(item)}\n\n"

            return StreamingResponse(
                generate_stream(),
                media_type="text/event-stream"
            )

    except Exception as e:
        logger.error(f"Error during FAQ generation: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Something went wrong. Please try again later.")

@app.get("/api/companies/most-viewed")
async def get_most_viewed_companies():
    """
    Get the most viewed companies
    """
    return {
        "status": "success",
        "data": get_all_companies()
    }


@app.get("/api/companies/{ticker}/key-stats")
async def get_key_stats(ticker: str):
    """
    Get key stats for a given ticker symbol
    """
    key_stats =  get_key_stats_for_ticker(ticker)
    return {
        "status": "success",
        "data": key_stats
    }

@app.post("/api/companies/{ticker}/upload_report")
async def upload_10k_report(
    ticker: str, 
    file: UploadFile = File(...),
    extract_revenue: bool = False,
    extract_insights: bool = False
):
    """
    Upload and process a 10-K report PDF file
    
    Args:
        file (UploadFile): The PDF file to be uploaded
        ticker (str): Company ticker symbol
        extract_revenue (bool): Whether to extract revenue data from the report
        extract_insights (bool): Whether to extract insights from the report
    Returns:
        dict: Processed financial data
    """
    try:
        if not file.filename or not file.filename.endswith('.pdf'):
            raise HTTPException(
                status_code=400,
                detail="Only PDF files are accepted"
            )
            
        file_content = await file.read()
        # Get the year from filename
        year = int(file.filename.split("_")[0])
        result = await handle_company_report(file_content, ticker, year, extract_revenue, extract_insights)
        
        return {
            "status": "success",
            "data": result,
            "message": "10-K report processed successfully"
        }
        
    except Exception as e:
        logger.error(f"Error processing 10-K report: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Error processing the uploaded file: {str(e)}"
        )

@app.get("/api/companies/{ticker}/swot")
async def get_swot(ticker: str):
    swot = await get_swot_analysis_for_ticker(ticker)
    return {
        "status": "success",
        "data": swot
    }

def is_number(value):
    try:
        float(value)
        return True
    except (ValueError, TypeError):
        return False

def parse_financial_statements(df):
    """
    Parse financial statements data into a standardized format.
    
    Args:
        df: List of dictionaries containing financial metrics with TTM and yearly values
        
    Returns:
        List of dictionaries with standardized financial statement format
    """
    # Initialize a dictionary to store metrics by year
    yearly_metrics = {}
    
    # First pass: collect all metrics for each year
    for item in df:
        metric_name = item['Breakdown']
        
        # Process TTM data
        if 'TTM' in item and item['TTM'] is not None and is_number(item['TTM']):
            if 'TTM' not in yearly_metrics:
                yearly_metrics['TTM'] = {}
            yearly_metrics['TTM'][metric_name] = int(item['TTM'])
        
        # Process yearly data
        for date_str, value in item.items():
            if date_str not in ['Breakdown', 'TTM'] and value is not None and is_number(value):
                # Convert date string to year
                date = datetime.strptime(date_str, '%m/%d/%Y')
                year = date.year
                
                if year not in yearly_metrics:
                    yearly_metrics[year] = {}
                yearly_metrics[year][metric_name] = int(value)
    
    # Second pass: convert to desired output format
    result = []
    for period_end_year, metrics in yearly_metrics.items():
        item = {
            'period_end_year': period_end_year,
            'is_ttm': period_end_year == 'TTM',
            'period_type': 'annually',
            'income_statement': metrics
        }
        result.append(item)
    
    return result
