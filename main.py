import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from ai_models.model_mapper import map_frontend_model_to_enum
from connectors.conversation_store import (
    append_assistant_message,
    append_user_message,
    generate_conversation_id,
    get_conversation_history_for_prompt,
)
from faq_generator import get_frequent_ask_questions_for_ticker_stream, get_general_frequent_ask_questions
from services.company import (
    PeriodType,
    get_all_companies,
    get_company_financial_statements,
    get_key_stats_for_ticker,
    get_swot_analysis_for_ticker,
    handle_company_report,
)
from services.company_filings import analyze_financial_report, analyze_uploaded_file, get_company_filings
from services.company_insight import InsightType, fetch_insights_for_ticker, get_insights_for_ticker
from services.company_report import generate_detailed_report_for_insight, generate_dynamic_report_for_insight
from services.etf import get_all_etfs, get_etf_by_ticker
from services.financial_analyzer import FinancialAnalyzer
from services.revenue_data import get_revenue_breakdown_for_company
from services.revenue_insight import get_revenue_insights_for_company_product, get_revenue_insights_for_company_region
from utils.logging import setup_local_logging, setup_production_logging

load_dotenv()

environment = os.getenv("ENV", "local").lower()
log_level = os.getenv("LOG_LEVEL", "INFO").upper()

# Setup logging based on environment
if environment == "local":
    setup_local_logging(log_level)
else:
    setup_production_logging(log_level)

# Mute specific loggers
logging.getLogger("google_genai").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Initialize financial analyzer
financial_analyzer = FinancialAnalyzer()

# FastAPI application instance
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


@app.get("/api/healthcheck")
async def healthcheck():
    return {"success": True}


@app.get("/api/etf")
async def get_etfs():
    """
    Get all available ETFs for display on home page

    Returns:
        JSON with data array containing ETF list items (ticker, name, fund_provider)
    """
    try:
        etfs = await get_all_etfs()
        return {"data": etfs}
    except Exception as e:
        logger.error(f"Error fetching ETF list: {e}")
        raise HTTPException(status_code=500, detail="Internal server error while fetching ETF list")


class ReportType(Enum):
    INCOME_STATEMENT = "income_statement"
    BALANCE_SHEET = "balance_sheet"
    CASH_FLOW = "cash_flow"


@app.get("/api/companies/{ticker}/statements")
def get_financial_statements(ticker: str, report_type: str | None = None, period_type: str | None = None):
    # Validate report_type if provided
    if report_type:
        try:
            ReportType(report_type)
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"Invalid report type. Must be one of: {[rt.value for rt in ReportType]}"
            )

    # Validate period_type if provided
    if period_type:
        try:
            PeriodType(period_type)
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"Invalid period type. Must be one of: {[pt.value for pt in PeriodType]}"
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

    return StreamingResponse(generate_insights(), media_type="text/event-stream")


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

    return StreamingResponse(generate_insights(), media_type="text/event-stream")


@app.post("/api/companies/{ticker}/analyze")
async def analyze_financial_data(ticker: str, request: Request):
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
        question = body.get("question")
        use_google_search = body.get("useGoogleSearch", False)
        use_url_context = body.get("useUrlContext", False)
        deep_analysis = body.get("deepAnalysis", False)
        preferred_model_str = body.get("preferredModel", "fastest")
        conversation_id = body.get("conversationId")

        # Map and validate the model name early
        preferred_model = map_frontend_model_to_enum(preferred_model_str)

        if not question:
            raise HTTPException(status_code=400, detail="Question is required in request body")

        # Normalize ticker: treat "undefined", "null", empty string as no ticker
        normalized_ticker = ticker.strip().upper() if ticker else ""
        if normalized_ticker in ["UNDEFINED", "NULL", ""]:
            normalized_ticker = ""
            logger.debug(f"🔧 Normalized ticker '{ticker}' to empty (no ticker context)")

        # Get or create anonymous user ID from cookie
        anon_user_id = request.cookies.get("anon_user_id")
        if not anon_user_id:
            anon_user_id = str(uuid.uuid4())
            logger.info(f"🔐 Generated new anonymous user ID: {anon_user_id[:8]}...")
        else:
            logger.debug(f"🔐 Using existing anonymous user ID: {anon_user_id[:8]}...")

        # Generate conversation ID if not provided
        if not conversation_id:
            conversation_id = generate_conversation_id()
            logger.info(f"💬 Generated new conversation ID: {conversation_id} (ticker: {normalized_ticker or 'none'})")
        else:
            logger.info(f"💬 Using existing conversation ID: {conversation_id} (ticker: {normalized_ticker or 'none'})")

        # Load conversation history (use normalized ticker for storage key)
        conversation_messages = get_conversation_history_for_prompt(
            anon_user_id, normalized_ticker or "none", conversation_id
        )
        if conversation_messages:
            num_pairs = len(conversation_messages) // 2
            logger.info(
                f"📚 Retrieved {num_pairs} Q/A pair(s) from conversation history "
                f"(user: {anon_user_id[:8]}..., ticker: {ticker.upper()}, conv: {conversation_id[:8]}...)"
            )
        else:
            logger.info(
                f"📚 No conversation history found (new conversation) "
                f"(user: {anon_user_id[:8]}..., ticker: {ticker.upper()}, conv: {conversation_id[:8]}...)"
            )

        # Append user message to conversation before generation (use normalized ticker)
        append_user_message(anon_user_id, normalized_ticker or "none", conversation_id, question)
        logger.debug(f"💾 Stored user message in conversation {conversation_id[:8]}...")

        async def generate_analysis():
            try:
                # Emit conversation ID early in the stream
                yield json.dumps({"type": "conversation", "body": {"conversationId": conversation_id}}) + "\n\n"

                # Buffer assistant output for persistence
                assistant_output_buffer = []

                async for chunk in financial_analyzer.analyze_question(
                    normalized_ticker or ticker,  # Use normalized ticker, fallback to original for display
                    question,
                    use_google_search,
                    use_url_context,
                    deep_analysis,
                    preferred_model,
                    conversation_messages=conversation_messages,
                    conversation_id=conversation_id,
                    anon_user_id=anon_user_id,
                ):
                    # Check if the client has disconnected
                    if await request.is_disconnected():
                        return

                    # Buffer answer chunks for persistence
                    if chunk.get("type") == "answer":
                        assistant_output_buffer.append(chunk.get("body", ""))

                    # Each chunk is now a JSON object with type and body
                    yield json.dumps(chunk) + "\n\n"

                # After streaming completes, append assistant message to conversation (use normalized ticker)
                if assistant_output_buffer:
                    assistant_full_text = "".join(assistant_output_buffer)
                    append_assistant_message(
                        anon_user_id, normalized_ticker or "none", conversation_id, assistant_full_text
                    )
                    logger.debug(
                        f"💾 Stored assistant response in conversation {conversation_id[:8]}... "
                        f"({len(assistant_output_buffer)} chunks, {len(assistant_full_text)} chars)"
                    )

            except asyncio.CancelledError:
                # Handle cancellation
                logger.info("Client cancelled request to analyze financial data", {"ticker": ticker})
                return

        # Create response with cookie setting
        response = StreamingResponse(generate_analysis(), media_type="text/event-stream")

        # Set cookie if it wasn't present (for first-time users)
        if not request.cookies.get("anon_user_id"):
            # Determine cookie attributes based on environment
            is_production = environment.lower() == "production"
            response.set_cookie(
                key="anon_user_id",
                value=anon_user_id,
                max_age=86400 * 365,  # 1 year
                httponly=True,
                samesite="None" if is_production else "Lax",
                secure=is_production,  # Secure flag only in production
            )

        return response
    except Exception as e:
        logger.error(f"Error during analysis: {str(e)}")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again later.")


@app.post("/api/companies/{ticker}/files/analyze")
async def analyze_financial_data_with_file(
    ticker: str,
    file: UploadFile = File(...),
    question: str = Form(...),
):
    """
    Analyze financial statements for a given ticker symbol based on a specific question,
    with support for file upload (multipart/form-data).
    Streams the results using Server-Sent Events.

    Args:
        ticker (str): Company ticker symbol
        file (UploadFile): PDF file to be analyzed
        question (str): The question to answer about the financial data

    Returns:
        StreamingResponse: Server-sent events stream of analysis results
    """
    try:
        # Validate file type
        if not file.filename or not file.filename.endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Only PDF files are accepted")

        # Read file content and validate size
        file_content = await file.read()
        file_size_mb = len(file_content) / (1024 * 1024)
        max_size_mb = 20

        if file_size_mb > max_size_mb:
            raise HTTPException(
                status_code=400,
                detail=f"File size ({file_size_mb:.2f}MB) exceeds maximum allowed size of {max_size_mb}MB",
            )

        # Log file upload
        logger.info(
            f"Received file upload: {file.filename} ({file_size_mb:.2f}MB) for ticker {ticker.upper()} with question: {question}"
        )

        # Stream analysis from service layer
        async def generate_analysis():
            async for chunk in analyze_uploaded_file(
                ticker=ticker.upper(),
                question=question,
                file_content=file_content,
                filename=str(file.filename),
            ):
                yield json.dumps(chunk) + "\n\n"

        return StreamingResponse(generate_analysis(), media_type="text/event-stream")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during file-based analysis: {str(e)}")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again later.")


@app.get("/api/company/faq")
async def get_faq(request: Request):
    """
    Suggest 3 FAQs for a given ticker symbol
    """
    try:
        # Get ticker symbol from query params
        ticker = request.query_params.get("ticker")
        stream = request.query_params.get("stream")

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

            return StreamingResponse(generate_stream(), media_type="text/event-stream")

        # If stream parameter is provided and is "true", use streaming response
        if stream and stream.lower() == "true":

            async def generate_stream():
                async for item in get_frequent_ask_questions_for_ticker_stream(ticker):
                    yield f"data: {json.dumps(item)}\n\n"

            return StreamingResponse(generate_stream(), media_type="text/event-stream")

    except Exception as e:
        logger.error(f"Error during FAQ generation: {str(e)}")
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again later.")


@app.get("/api/companies/most-viewed")
async def get_most_viewed_companies():
    """
    Get the most viewed companies
    """
    return {"status": "success", "data": get_all_companies()}


@app.get("/api/companies/{ticker}/key-stats")
async def get_key_stats(ticker: str):
    """
    Get key stats for a given ticker symbol
    """
    key_stats = get_key_stats_for_ticker(ticker.upper())

    return {"status": "success", "data": key_stats.__dict__ if key_stats else None}


@app.get("/api/companies/{ticker}/filings/{period}")
async def get_filings(ticker: str, period: str):
    """
    Get 10K filings for a given ticker symbol and period

    Args:
        ticker (str): Company ticker symbol
        period (str): Period type - must be 'annual' or 'quarterly'

    Returns:
        List of filing objects with url and period_end_year
    """
    # Validate period parameter
    if period not in ["annual", "quarterly"]:
        raise HTTPException(status_code=400, detail="Invalid period. Must be 'annual' or 'quarterly'")

    try:
        filings = get_company_filings(ticker, period)
        return filings
    except Exception as e:
        logger.error(f"Error fetching filings for {ticker}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching company filings")


@app.post("/api/companies/{ticker}/upload_report")
async def upload_10k_report(
    ticker: str, file: UploadFile = File(...), extract_revenue: bool = False, extract_insights: bool = False
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
        if not file.filename or not file.filename.endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Only PDF files are accepted")

        file_content = await file.read()
        # Get the year from filename
        year = int(file.filename.split("_")[0])
        result = await handle_company_report(file_content, ticker, year, extract_revenue, extract_insights)

        return {"status": "success", "data": result, "message": "10-K report processed successfully"}

    except Exception as e:
        logger.error(f"Error processing 10-K report: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing the uploaded file: {str(e)}")


@app.get("/api/companies/{ticker}/swot")
async def get_swot(ticker: str):
    swot = await get_swot_analysis_for_ticker(ticker)
    return {"status": "success", "data": swot}


@app.get("/api/companies/{ticker}/insights/{type}")
async def get_insights(
    ticker: str,
    type: InsightType,
    stream: Optional[bool] = Query(
        None,
        description="Set to True for streaming response, False for full JSON response. Defaults to streaming if not specified.",
    ),
):
    print("Stream", stream)
    # Validate type
    if type not in InsightType:
        raise HTTPException(status_code=400, detail="Invalid insight type")

    if stream is True or stream is None:

        async def generate_insights():
            async for insight in get_insights_for_ticker(ticker, type):
                yield f"{json.dumps(insight)}\n\n"

        return StreamingResponse(generate_insights(), media_type="text/event-stream")

    # No streaming, fetch from DB
    return {"status": "success", "data": fetch_insights_for_ticker(ticker, type)}


@app.get("/api/companies/{ticker}/reports/{slug}")
async def generate_report_for_insight(ticker: str, slug: str):
    """
    Generate a report for a given insight already persisted in the database
    """

    async def generate_report():
        async for report in generate_detailed_report_for_insight(ticker.upper(), slug):
            yield f"{json.dumps(report)}\n\n"

    return StreamingResponse(generate_report(), media_type="text/event-stream")


@app.get("/api/companies/{ticker}/dynamic-report/{slug}")
async def generate_report_for_insight_dynamic(ticker: str, slug: str):
    """
    Generate a dynamic report for a given insight
    """

    async def generate_report():
        async for report in generate_dynamic_report_for_insight(ticker.upper(), slug):
            yield f"{json.dumps(report)}\n\n"

    return StreamingResponse(generate_report(), media_type="text/event-stream")


@app.post("/api/companies/{ticker}/reports/analyze")
async def analyze_company_report(
    ticker: str,
    request: Request,
    period_end_at: str = Query(..., description="Year or quarter ending of the report"),
    period_type: PeriodType = Query(PeriodType.ANNUALLY, description="Period type - annual or quarterly"),
) -> StreamingResponse:
    """
    Analyze a company report (10K/10Q) for a given ticker and period

    Args:
        ticker (str): Company ticker symbol
        request (Request): FastAPI request object to read optional JSON body with deepAnalysis flag
        period_end_at (str): Year or quarter ending of the report. Could be "2024" for annual or "6/30/2025" for quarterly
        period_type (PeriodType): Period type - annual or quarterly

    Returns:
        Streaming analysis results
    """
    try:
        # Parse optional JSON body for deepAnalysis flag
        deep_analysis = False
        try:
            body = await request.json()
            deep_analysis = body.get("deepAnalysis", False)
        except Exception:
            # If body is missing or invalid, default to short analysis (deep_analysis=False)
            pass
        # Validate ticker format
        ticker = ticker.upper().strip()
        if not ticker or len(ticker) > 10:
            raise HTTPException(status_code=400, detail="Invalid ticker symbol")

        # Validate period_end_at based on period_type
        current_year = datetime.now().year
        max_year = current_year + 1

        if period_type == PeriodType.ANNUALLY:
            try:
                year = int(period_end_at)
                if year < 1990 or year > max_year:
                    raise ValueError()
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"For annual reports, period_end_at must be a valid year between 1990 and {max_year}",
                )
        else:  # Quarterly
            try:
                month, day, year = map(int, period_end_at.split("/"))
                if year < 1990 or year > max_year or month < 1 or month > 12 or day < 1 or day > 31:
                    raise ValueError()
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail=f"For quarterly reports, period_end_at must be in MM/DD/YYYY format with valid date (year between 1990 and {max_year})",
                )

        # TODO: Implement business logic for report analysis
        # This will include:
        # 1. Fetch filing data for the specified year and period
        # 2. Extract key financial metrics and insights
        # 3. Generate AI-powered analysis
        # 4. Return structured analysis results

        async def generate_analysis():
            # Stream the AI analysis from the service layer
            async for analysis_chunk in analyze_financial_report(
                ticker, period_end_at, period_type.value, deep_analysis
            ):
                yield f"data: {json.dumps(analysis_chunk)}\n\n"

        return StreamingResponse(generate_analysis(), media_type="text/event-stream")

    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid input: {str(e)}")
    except Exception as e:
        logger.error(f"Error analyzing report for {ticker} ({period_end_at}): {str(e)}")
        raise HTTPException(status_code=500, detail="Error processing report analysis request")


@app.get("/api/etf/{ticker}")
async def get_etf(ticker: str):
    """
    Get ETF fundamental data by ticker symbol

    Args:
        ticker (str): ETF ticker symbol (e.g., 'SXR8', 'CSPX')

    Returns:
        ETF fundamental data including holdings, sectors, and country allocation
    """
    etf = get_etf_by_ticker(ticker.upper())
    if not etf:
        raise HTTPException(status_code=404, detail=f"ETF with ticker '{ticker}' not found")
    return etf
