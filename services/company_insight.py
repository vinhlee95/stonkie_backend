from logging import getLogger
from connectors.company_financial import CompanyFinancialConnector
from agent.agent import Agent
import json
from sqlalchemy.inspection import inspect
from datetime import datetime
from typing import AsyncGenerator, Dict, Any

logger = getLogger(__name__)

company_financial_connector = CompanyFinancialConnector()
agent = Agent(model_type="gemini")

def to_dict(model_instance) -> Dict[str, Any]:
    """Convert SQLAlchemy model to dictionary, handling datetime fields"""
    result = {}
    for c in inspect(model_instance).mapper.column_attrs:
        value = getattr(model_instance, c.key)
        # Convert datetime objects to ISO format strings
        if isinstance(value, datetime):
            value = value.isoformat()
        result[c.key] = value
    return result

async def get_growth_insights_for_ticker(ticker: str) -> AsyncGenerator[Dict[str, Any], None]:
    try:
      annual_financial_statements = company_financial_connector.get_company_financial_statements(ticker)
      quarterly_financial_statements = company_financial_connector.get_company_quarterly_financial_statements(ticker)
      
      annual_financial_statements_json = [to_dict(item) for item in annual_financial_statements]
      quarterly_financial_statements_json = [to_dict(item) for item in quarterly_financial_statements]

      prompt = f"""
          You are a financial analyst tasked with analyzing growth data for {ticker}. 
          The data shows revenue, net income, and other financial metrics over multiple years.
          Generate insights for following metrics:
          - revenue
          - net income
          - net margin
          - profits
          
          Here is the annual financial data:
          {json.dumps(annual_financial_statements_json, indent=2)}

          Here is the quarterly financial data:
          {json.dumps(quarterly_financial_statements_json, indent=2)}
      """

      response = await agent.generate_content(prompt=prompt, stream=True)
      async for chunk in response:
          if hasattr(chunk, 'text'):
              yield {"type": "insight", "content": chunk.text}
    
    except Exception as e:
        logger.error(f"Error getting growth insights for company", {
            "ticker": ticker,
            "error": str(e)
        })
        yield {"type": "error", "content": "Error getting growth insights for company"}

def get_earnings_insights_for_ticker(ticker: str):
    pass

def get_cash_flow_insights_for_ticker(ticker: str):
    pass