import os
from dotenv import load_dotenv
import google.generativeai as genai
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
from typing import Dict

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

OUTPUT_DIR = "outputs"
app = FastAPI()

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Add your React app's URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/financial-data/{ticker}/{report_type}")
async def get_financial_data(ticker: str, report_type: str) -> Dict:
    """
    Get financial data for a specific ticker and report type
    report_type can be: income_statement, balance_sheet, or cash_flow
    """
    try:
        file_path = os.path.join(OUTPUT_DIR, f"{ticker.lower()}_{report_type}.csv")
        if not os.path.exists(file_path):
            # Return 200 but with an empty data object
            return {
                "data": [],
                "columns": []
            }
        
        print(f"Already exported {ticker.lower()}_{report_type}.csv")
        # Read CSV and convert to JSON
        df = pd.read_csv(file_path)
        print(df)
        print(df.columns.tolist())
        print(df.to_dict('records'))
        
        return {
            "data": df.to_dict('records'),
            "columns": df.columns.tolist()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
