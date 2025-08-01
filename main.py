import json
import logging
from typing import Optional
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from analyzer import analyze_financial_data_from_question
from enum import Enum
from services.company import get_key_stats_for_ticker, handle_company_report, get_swot_analysis_for_ticker, get_all_companies
from services.revenue_insight import get_revenue_insights_for_company_product, get_revenue_insights_for_company_region
from services.revenue_data import get_revenue_breakdown_for_company
from services.company import get_company_financial_statements
from services.company_insight import fetch_insights_for_ticker, get_insights_for_ticker, InsightType
from services.company_report import generate_detailed_report_for_insight, generate_dynamic_report_for_insight
from faq_generator import get_general_frequent_ask_questions, get_frequent_ask_questions_for_ticker_stream
from fastapi.responses import StreamingResponse
import asyncio
import os
import sys

load_dotenv()

environment = os.getenv('ENV', 'local').lower()

# Get log level from environment variable, default to INFO
log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
numeric_level = getattr(logging, log_level, logging.INFO)

# Configure logging with more detailed format
if environment == 'local':
    logging.basicConfig(
        level=numeric_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        stream=sys.stdout  # Direct all logging to stdout
    )
else:
    logging.basicConfig(
        level=numeric_level,
        format='%(name)s - %(levelname)s - %(message)s',
        stream=sys.stdout  # Direct all logging to stdout
    )

# Mute specific loggers
logging.getLogger("google_genai").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

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


class PeriodType(Enum):
    ANNUALLY = "annually"
    QUARTERLY = "quarterly"

@app.get("/api/companies/{ticker}/statements")
def get_financial_statements(ticker: str, report_type: str | None = None, period_type: str | None = None):
    # Validate report_type if provided
    if report_type:
        try:
            ReportType(report_type)
        except ValueError:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid report type. Must be one of: {[rt.value for rt in ReportType]}"
            )

    # Validate period_type if provided
    if period_type:
        try:
            PeriodType(period_type)
        except ValueError:
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid period type. Must be one of: {[pt.value for pt in PeriodType]}"
            )

    statements = get_company_financial_statements(ticker, report_type, period_type)
    return statements
    
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
        use_google_search = body.get('useGoogleSearch', False)
        
        if not question:
            raise HTTPException(status_code=400, detail="Question is required in request body")

        async def generate_analysis():
            try:
                async for chunk in analyze_financial_data_from_question(ticker, question, use_google_search):
                    # Check if the client has disconnected
                    if await request.is_disconnected():
                        return
                    # Each chunk is now a JSON object with type and body
                    yield json.dumps(chunk) + "\n\n"
            except asyncio.CancelledError:
                # Handle cancellation
                logger.info('Client cancelled request to analyze financial data', {"ticker": ticker})
                return

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
    key_stats =  get_key_stats_for_ticker(ticker.upper())

    return {
        "status": "success",
        "data": key_stats.__dict__ if key_stats else None
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

@app.get("/api/companies/{ticker}/insights/{type}")
async def get_insights(
    ticker: str, 
    type: InsightType,
    stream: Optional[bool] = Query(None, description="Set to True for streaming response, False for full JSON response. Defaults to streaming if not specified.")
):
    print("Stream", stream)
    # Validate type
    if type not in InsightType:
        raise HTTPException(status_code=400, detail="Invalid insight type")
        
    if stream == True or stream is None:
        async def generate_insights():
            async for insight in get_insights_for_ticker(ticker, type):
                yield f"{json.dumps(insight)}\n\n"

        return StreamingResponse(
            generate_insights(),
            media_type="text/event-stream"
        )
    
    # No streaming, fetch from DB
    return {
        "status": "success",
        "data": fetch_insights_for_ticker(ticker, type)
    }

@app.get("/api/companies/{ticker}/reports/{slug}")
async def generate_report_for_insight(ticker: str, slug: str):
    """
    Generate a report for a given insight already persisted in the database
    """
    async def generate_report():
        async for report in generate_detailed_report_for_insight(ticker.upper(), slug):
            yield f"{json.dumps(report)}\n\n"

    return StreamingResponse(
        generate_report(),
        media_type="text/event-stream"
    )

@app.get("/api/companies/{ticker}/dynamic-report/{slug}")
async def generate_report_for_insight_dynamic(ticker: str, slug: str):
    """
    Generate a dynamic report for a given insight
    """
    async def generate_report():
        async for report in generate_dynamic_report_for_insight(ticker.upper(), slug):
            yield f"{json.dumps(report)}\n\n"

    return StreamingResponse(
        generate_report(),
        media_type="text/event-stream"
    )