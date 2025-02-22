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
from services.company import get_key_stats_for_ticker, get_revenue_breakdown_for_company, handle_10k_file
from faq_generator import get_general_frequent_ask_questions, get_frequent_ask_questions_for_ticker_stream
from pydantic import BaseModel
from urllib.parse import urlencode
import time
from functools import lru_cache
from google.api_core import retry
from google.cloud.storage import Client
from fastapi.responses import StreamingResponse

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
    allow_origins=["http://localhost:3000", "https://stonkie.netlify.app"], 
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

    

def get_company_logo_url(company_name: str):
    """
    Proxy endpoint to fetch company logo and return as image response
    """
    API_KEY = os.getenv('BRAND_FETCH_API_KEY')
    params = urlencode({'c': API_KEY })
    return f"https://cdn.brandfetch.io/{company_name.lower()}.com/w/100/h/100?{params}"


@app.get("/api/companies/most-viewed")
async def get_most_viewed_companies():
    """
    Get the most viewed companies
    """
    class Company(BaseModel):
        name: str
        ticker: str
        logo_url: str

    most_viewed_companies: list[Company] = [
        Company(name="Apple", ticker="AAPL", logo_url=get_company_logo_url("apple")),
        Company(name="Tesla", ticker="TSLA", logo_url=get_company_logo_url("tesla")),
        Company(name="Microsoft", ticker="MSFT", logo_url=get_company_logo_url("microsoft")),
        Company(name="Nvidia", ticker="NVDA", logo_url=get_company_logo_url("nvidia")),
        Company(name="Nordea", ticker="NDA-FI.HE", logo_url=get_company_logo_url("nordea")),
        Company(name="Mandatum", ticker="MANTA.HE", logo_url=get_company_logo_url("mandatum")),
        Company(name="Fortum", ticker="FORTUM.HE", logo_url=get_company_logo_url("fortum")),
        Company(name="Alphabet", ticker="GOOG", logo_url=get_company_logo_url("google")),
        Company(name="Amazon", ticker="AMZN", logo_url=get_company_logo_url("amazon")),
        Company(name="Meta", ticker="META", logo_url=get_company_logo_url("meta")),
        Company(name="Netflix", ticker="NFLX", logo_url=get_company_logo_url("netflix")),
        Company(name="Berkshire Hathaway", ticker="BRK.A", logo_url=get_company_logo_url("berkshire")),
        Company(name="Wallmart", ticker="WMT", logo_url=get_company_logo_url("walmart")),
        Company(name="AT&T", ticker="T", logo_url=get_company_logo_url("att")),
        Company(name="Coca Cola", ticker="KO", logo_url=get_company_logo_url("coca-cola")),
    ]
    
    return {
        "status": "success",
        "data": most_viewed_companies
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

@app.post("/api/companies/{ticker}/10k/{year}")
async def upload_10k_report(ticker: str, year: int, file: UploadFile = File(...)):
    """
    Upload and process a 10-K report PDF file
    
    Args:
        file (UploadFile): The PDF file to be uploaded
        ticker (str): Company ticker symbol
    Returns:
        dict: Processed financial data
    """
    try:
        if not file.filename.endswith('.pdf'):
            raise HTTPException(
                status_code=400,
                detail="Only PDF files are accepted"
            )
            
        file_content = await file.read()
        result = await handle_10k_file(file_content, ticker, year)
        
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

