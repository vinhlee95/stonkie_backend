import base64
import json
import os
from dotenv import load_dotenv
import google.generativeai as genai
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
from typing import Dict
from google.cloud import storage
from google.oauth2 import service_account
import logging
from analyzer import analyze_financial_data_from_question

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

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
    allow_origins=["http://localhost:3000", "https://stock-agent.netlify.app"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health check
@app.get("/api/health")
async def health_check():
    return {"status": "ok"}

@app.get("/api/financial-data/{ticker}/{report_type}")
async def get_financial_data(ticker: str, report_type: str) -> Dict:
    """
    Get financial data for a specific ticker and report type
    report_type can be: income_statement, balance_sheet, or cash_flow
    """
    try:
        # Get the CSV from google cloud storage
        credentials = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
        if not credentials:
            print("❌ Google credentials not found in environment variables")
            return {
                "data": [],
            }

        credentials_dict = json.loads(base64.b64decode(credentials).decode('utf-8'))
        credentials = service_account.Credentials.from_service_account_info(credentials_dict)
        storage_client = storage.Client(credentials=credentials)

        csv_blob = storage_client.bucket(BUCKET_NAME).blob(f"{ticker.lower()}_{report_type}.csv")
        
        # If the CSV doesn't exist, return an empty data object
        if not csv_blob.exists():
            return {
                "data": [],
                "columns": []
            }
        
        # Download the blob as string and convert to bytes
        csv_content = csv_blob.download_as_string()
        
        # Use pandas to read the CSV content from the string
        df = pd.read_csv(pd.io.common.BytesIO(csv_content))
        
        # Convert the dataframe to JSON format
        return {
            "data": df.to_dict('records'),  # Each row becomes a dictionary
            "columns": df.columns.tolist()   # List of column names
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/{ticker}/analyze")
async def analyze_financial_data(ticker: str, request: Request):
    """
    Analyze financial statements for a given ticker symbol based on a specific question
    
    Args:
        ticker (str): Ticker symbol from URL path parameter
        request (Request): FastAPI request object containing the question in body
    Returns:
        dict: Analysis response and status
    """
    try:
        body = await request.json()
        question = body.get('question')
        
        if not question:
            raise HTTPException(status_code=400, detail="Question is required in request body")

        analysis_result = analyze_financial_data_from_question(ticker, question)

        return {
            "status": "success",
            "data": analysis_result
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error during analysis: {str(e)}")