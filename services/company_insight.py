from logging import getLogger
from connectors.company_financial import CompanyFinancialConnector
from agent.agent import Agent
import json
from sqlalchemy.inspection import inspect
from datetime import datetime
from typing import AsyncGenerator, Dict, Any
from connectors.company_insight import CompanyInsightConnector, CreateCompanyInsightDto, CompanyInsightDto
import uuid
from enum import Enum
import requests
from urllib.parse import urlencode
import os
from connectors.company import get_by_ticker

UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY")

logger = getLogger(__name__)

company_financial_connector = CompanyFinancialConnector()
company_insight_connector = CompanyInsightConnector()
agent = Agent(model_type="gemini")

async def fetch_unsplash_image(ticker: str) -> str:
    """
    Fetch a thumbnail image from Unsplash for a given company ticker.
    Return the full url of the image for now. 
    Store different sizes if further optimizations are needed.
    """
    company_name = get_by_ticker(ticker).name
    
    async def generate_image_query(company_name: str) -> str:
        agent = Agent(model_type="gemini")
        prompt = f"""
            Generate a query to search for an image of {company_name} from an API.
            The query should be a single sentence and less than 5 words. No need to have "image" in the query.
            The query should be a product or a service that the company offers.
            For example, if the company is Apple, the query should be "Apple iPhone".
            If the company is Tesla, the query should be "Tesla Gigafactory".
        """
        response = await agent.generate_content(prompt)
        await response.resolve()
        return response.text 

    query = await generate_image_query(company_name)

    if company_name:
        params = {
            'query': query,
            'page': 1,
            'per_page': 1,
            'orientation': 'landscape'
        }
    
    headers = {
        'Authorization': f'Client-ID {UNSPLASH_ACCESS_KEY}'
    }
    
    response = requests.get(
        f'https://api.unsplash.com/search/photos?{urlencode(params)}',
        headers=headers
    )
    
    res = response.json()
    result = res.get("results")[0]
    url = result.get("urls").get("full")

    return url
    

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

async def persist_insight(ticker: str, insight_type: str, content: str) -> CompanyInsightDto | None:
    """Persist an insight to the database."""
    try:
        slug = f"{ticker}-{insight_type}-{uuid.uuid4().hex[:8]}"
        thumbnail_url = await fetch_unsplash_image(ticker)

        insight_dto = CreateCompanyInsightDto(
            company_symbol=ticker,
            slug=slug,
            insight_type=insight_type,
            content=content,
            thumbnail_url=thumbnail_url
        )
        new_insight = company_insight_connector.create_one(insight_dto)
        return new_insight
    except Exception as e:
        logger.error(f"Error persisting insight to database", {
            "ticker": ticker,
            "insight_type": insight_type,
            "error": str(e)
        })
        return None
async def process_streaming_insights(response, ticker: str) -> AsyncGenerator[dict, None]:
    """Process streaming insights from the AI response."""
    accumulated_text = ""
    current_insight = ""
    in_insight = False
    
    async for chunk in response:
        chunk_text = chunk.text
        accumulated_text += chunk_text
        
        # Process any complete insights
        while "---INSIGHT_START---" in accumulated_text and "---INSIGHT_END---" in accumulated_text:
            start_idx = accumulated_text.find("---INSIGHT_START---") + len("---INSIGHT_START---")
            end_idx = accumulated_text.find("---INSIGHT_END---")
            
            if start_idx > 0 and end_idx > start_idx:
                insight_text = accumulated_text[start_idx:end_idx].strip()
                # Persist the insight before yielding
                new_insight = await persist_insight(ticker, "growth", insight_text)
                if new_insight:
                    yield {"type": "success", "data": {"content": insight_text, "slug": new_insight.slug}}
                else:
                    yield {"type": "success", "data": {"content": insight_text}}

                # Remove processed insight from accumulated text
                accumulated_text = accumulated_text[end_idx + len("---INSIGHT_END---"):]
                current_insight = ""
                in_insight = False
        
        # Handle streaming content between insights
        if "---INSIGHT_START---" in accumulated_text and not in_insight:
            in_insight = True
            start_idx = accumulated_text.rfind("---INSIGHT_START---") + len("---INSIGHT_START---")
            current_insight = accumulated_text[start_idx:]
        elif in_insight:
            current_insight += chunk_text
        
        # Stream current insight if it's meaningful and doesn't contain markers
        if current_insight.strip() and not any(marker in current_insight for marker in ["---INSIGHT_START---", "---INSIGHT_END---", "---COMPLETE---"]):
            yield {"type": "stream", "content": current_insight.strip()}
            current_insight = ""
        
        # Check if we're done
        if "---COMPLETE---" in accumulated_text:
            break

class InsightType(Enum):
    GROWTH = "growth"
    EARNINGS = "earning"
    CASH_FLOW = "cash_flow"

async def get_growth_insights_for_ticker(ticker: str, type: InsightType) -> AsyncGenerator[Dict[str, Any], None]:
    try:
        # First check for existing insights
        existing_insights = company_insight_connector.get_all_by_ticker(ticker)
        if existing_insights:
            # Filter for growth insights and sort by creation date
            growth_insights = sorted(
                [insight for insight in existing_insights if insight.insight_type == type.value],
                key=lambda x: x.created_at,
            )
            
            if growth_insights:
                # Stream existing insights in the same format
                for insight in growth_insights:
                    yield {"type": "success", "data": {"content": insight.content, "cached": True, "slug": insight.slug}}
                return

        # If no existing insights, generate new ones
        annual_financial_statements_json = company_financial_connector.get_annual_income_statements(ticker)
        quarterly_financial_statements_json = company_financial_connector.get_quarterly_income_statements(ticker)

        prompt = f"""
            You are a seasoned financial analyst specializing in growth analysis. Your task is to analyze {ticker}'s growth trajectory and provide unique, actionable insights.

            Focus on these key growth dimensions:
            1. Revenue Growth Dynamics
               - Analyze growth patterns, seasonality, and acceleration/deceleration
               - Identify growth drivers and their sustainability
               - Consider both organic and inorganic growth factors

            2. Profitability Growth
               - Examine margin expansion/contraction trends
               - Analyze operating leverage and efficiency improvements
               - Evaluate cost structure and scalability

            3. Market Position & Competitive Growth
               - Assess market share dynamics
               - Evaluate competitive advantages and moats
               - Analyze growth relative to industry peers

            4. Future Growth Potential
               - Identify emerging growth opportunities
               - Assess risks to growth sustainability
               - Evaluate growth runway and potential catalysts

            Guidelines for your analysis:
            - Have 4 insights in total for all the growth dimensions. Each insight should have less than 200 words.
            - Be specific about time periods and trends
            - Connect insights across different growth dimensions
            - Highlight both positive and concerning patterns
            - Support insights with relevant data points
            - Consider both quantitative and qualitative factors

            Format each insight as follows:
            ---INSIGHT_START---
            [First line of the insight as a summary, less than 15 words, informative and catchy, make it bold in markdown format]
            [Your creative, well-supported insight]
            ---INSIGHT_END---

            Generate insights one at a time, ensuring each is thorough and valuable.
            End your analysis with "---COMPLETE---"

            Here is the annual financial data:
            {json.dumps(annual_financial_statements_json, indent=2)}

            Here is the quarterly financial data:
            {json.dumps(quarterly_financial_statements_json, indent=2)}
        """

        response = await agent.generate_content(prompt=prompt, stream=True)
        async for insight in process_streaming_insights(response, ticker):
            yield insight
    
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
