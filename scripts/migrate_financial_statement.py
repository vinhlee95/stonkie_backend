# Migrate financial statements from GCP bucket to database
import os
import json
import pandas as pd
import base64
from google.oauth2 import service_account
from google.cloud.storage import Client
from google.api_core import retry
import logging
from dotenv import load_dotenv
from datetime import datetime
from connectors.database import SessionLocal, Base, engine
from models.company_financial_statement import CompanyFinancialStatement

BUCKET_NAME = "stock_agent_financial_report"
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_storage_client():
    credentials = os.getenv('GOOGLE_APPLICATION_CREDENTIALS_JSON')
    if not credentials:
        print("❌ Google credentials not found in environment variables")
        return None
    
    credentials_dict = json.loads(base64.b64decode(credentials).decode('utf-8'))
    credentials = service_account.Credentials.from_service_account_info(credentials_dict)
    return Client(credentials=credentials)

def get_financial_statement(ticker: str, report_type: str):
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

def parse_financial_statements(df: list[dict]):
    """
    Parse financial statements data into a standardized format.
    
    Args:
        df: List of dictionaries containing financial metrics with TTM and yearly values
        
    Returns:
        List of dictionaries with standardized financial statement format
    """
    # Initialize a dictionary to store metrics by year
    yearly_metrics = {}
    ttm_metrics = {}
    
    # First pass: collect all metrics for each year and TTM separately
    for item in df:
        metric_name = item['Breakdown']
        
        # Process TTM data
        if 'TTM' in item and item['TTM'] is not None:
            try:
                value = float(item['TTM'])
                if pd.isna(value):
                    ttm_metrics[metric_name] = None
                else:
                    ttm_metrics[metric_name] = round(value, 2)
            except (ValueError, TypeError):
                ttm_metrics[metric_name] = None
        
        # Process yearly data
        for date_str, value in item.items():
            if date_str not in ['Breakdown', 'TTM'] and value is not None:
                # Convert date string to year
                date = datetime.strptime(date_str, '%m/%d/%Y')
                year = date.year
                
                if year not in yearly_metrics:
                    yearly_metrics[year] = {}
                try:
                    num_value = float(value)
                    if pd.isna(num_value):
                        yearly_metrics[year][metric_name] = None
                    else:
                        yearly_metrics[year][metric_name] = round(num_value, 2)
                except (ValueError, TypeError):
                    yearly_metrics[year][metric_name] = None
    
    # Second pass: convert to desired output format
    result = []
    
    # First add all yearly data
    for period_end_year, metrics in yearly_metrics.items():
        item = {
            'period_end_year': period_end_year,
            'is_ttm': False,
            'period_type': 'annually',
            'income_statement': metrics
        }
        result.append(item)
    
    # If we have TTM data, add it with the most recent year + 1
    if ttm_metrics:
        most_recent_year = max(yearly_metrics.keys()) if yearly_metrics else None
        if most_recent_year:
            ttm_item = {
                'period_end_year': most_recent_year + 1,
                'is_ttm': True,
                'period_type': 'annually',
                'income_statement': ttm_metrics
            }
            result.append(ttm_item)
    
    return result

def save_financial_statements(ticker: str, financial_statements: list[dict]):
    db = SessionLocal()
    try:
        for statement in financial_statements:
            db_statement = CompanyFinancialStatement(
                company_symbol=statement['company_symbol'],
                period_end_year=statement['period_end_year'],
                is_ttm=statement['is_ttm'],
                period_type=statement['period_type'],
                income_statement=statement['income_statement'],
                balance_sheet=statement['balance_sheet'],
                cash_flow=statement['cash_flow']
            )
            db.add(db_statement)
        db.commit()
        logger.info(f"Successfully saved financial statements for {ticker}")
    except Exception as e:
        db.rollback()
        logger.error(f"Error saving financial statements: {str(e)}")
    finally:
        db.close()

def main():
    ticker = input("Enter stock ticker symbol (e.g., TSLA, AAPL): ").strip()
    
    REPORT_TYPES = ["income_statement", "balance_sheet", "cash_flow"]
    financial_statements = {}
    for report_type in REPORT_TYPES:
        # Get the financial statement from the GCP bucket
        df, columns = get_financial_statement(ticker, report_type)
        
        if df is None:
            logger.error(f"Cannot find {report_type} for {ticker}")
            continue

        # Save the financial statement to the database
        financial_statements[report_type] = parse_financial_statements(df.to_dict('records'))
    
    # Group financial statements by year
    grouped_statements = {}
    for report_type, statements in financial_statements.items():
        for statement in statements:
            year = statement['period_end_year']
            if year not in grouped_statements:
                grouped_statements[year] = {
                    'company_symbol': ticker,
                    'period_end_year': year,
                    'is_ttm': statement['is_ttm'],
                    'period_type': statement['period_type'],
                    'income_statement': None,
                    'balance_sheet': None,
                    'cash_flow': None
                }
            
            # Add the statement data to the appropriate field
            if report_type == 'income_statement':
                grouped_statements[year]['income_statement'] = statement['income_statement']
            elif report_type == 'balance_sheet':
                grouped_statements[year]['balance_sheet'] = statement['income_statement']
            elif report_type == 'cash_flow':
                grouped_statements[year]['cash_flow'] = statement['income_statement']
    
    # Convert grouped statements to list for database insertion
    statements_to_save = list(grouped_statements.values())
    
    # Save to database
    save_financial_statements(ticker, statements_to_save)
    print(f"Saved all financial statements for {ticker}")

if __name__ == "__main__":
    main()