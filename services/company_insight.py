from logging import getLogger
from connectors.company_financial import CompanyFinancialConnector
from agent.agent import Agent
from ai_models.model_name import ModelName
import json
from sqlalchemy.inspection import inspect
from datetime import datetime
from typing import AsyncGenerator, Dict, Any
from connectors.company_insight import CompanyInsightConnector, CreateCompanyInsightDto, CompanyInsightDto, InsightType
import uuid
import requests
from urllib.parse import urlencode
import os
from connectors.company import get_by_ticker
import random
from pydantic import BaseModel

UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY")

class CompanyInsight(BaseModel):
    title: str
    content: str

logger = getLogger(__name__)

company_financial_connector = CompanyFinancialConnector()
company_insight_connector = CompanyInsightConnector()
agent = Agent(model_type="gemini", model_name=ModelName.GeminiFlashLite)

# Cache to store used queries for each ticker
_query_cache: Dict[str, set[str]] = {}

async def fetch_unsplash_image(ticker: str) -> str:
    """
    Fetch a thumbnail image from Unsplash for a given company ticker.
    Return the full url of the image for now. 
    Store different sizes if further optimizations are needed.
    """
    company_name = get_by_ticker(ticker).name
    
    async def generate_image_query(company_name: str) -> str:
        # Add randomization to the prompt to ensure different queries
        aspects = [
            "product",
            "headquarters",
            "technology",
            "innovation",
            "manufacturing",
            "research",
            "development",
            "facility",
            "store",
            "office",
            "customer",
            "employee",
            "supply chain",
            "logistics",
            "distribution",
            "retail",
        ]
        
        # Initialize cache for this ticker if not exists
        if ticker not in _query_cache:
            _query_cache[ticker] = set()
            
        already_used_queries = _query_cache[ticker]
        print("already_used_queries", already_used_queries)
        
        aspect = random.choice(aspects)
        prompt = f"""
            You are a creative image search expert. Generate a search query for finding a relevant image of {company_name}.
            
            Requirements:
            - The query should be 2-3 words
            - Focus on the {aspect} aspect of the company
            - Be specific and descriptive
            - Avoid generic terms like "company" or "business"
            - Make it visually interesting and unique
            - The query should be something that would return high-quality, professional images
            
            Examples for different companies:
            - Apple: "Apple Vision Pro", "Apple Park aerial", "Apple Store interior"
            - Tesla: "Tesla Gigafactory", "Tesla Cybertruck", "Tesla charging station"
            - Microsoft: "Microsoft Surface Studio", "Microsoft campus aerial", "Microsoft data center"
            
            Previously used queries for this company (avoid repeating these):
            {already_used_queries}
            
            Generate a unique, creative query that hasn't been used before.
        """
        response = agent.generate_content(
            prompt, 
            model_name=ModelName.GeminiFlashLite,
            stream=False
        )
        query = response.text.strip()
        
        # Add the new query to cache
        _query_cache[ticker].add(query)
        return query

    query = await generate_image_query(company_name)
    print(query)

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
        logger.info("Persist insight for company", {
            "ticker": ticker,
            "insight_type": insight_type
        })
        return new_insight
    except Exception as e:
        logger.error(f"Error persisting insight to database", {
            "ticker": ticker,
            "insight_type": insight_type,
            "error": str(e)
        })
        return None

async def process_parsed_streaming_insights(ticker: str, insight: CompanyInsight, insight_type: InsightType) -> dict:
    new_insight = await persist_insight(ticker, insight_type, insight.content)
    return {"content": new_insight.content, "slug": new_insight.slug, "imageUrl": new_insight.thumbnail_url}

async def get_growth_insights_for_ticker(ticker: str) -> AsyncGenerator[Dict[str, Any], None]:
    try:
        # First check for existing insights
        existing_insights = company_insight_connector.get_all_by_ticker(ticker)
        if existing_insights:
            # Filter for growth insights and sort by creation date
            growth_insights = sorted(
                [insight for insight in existing_insights if insight.insight_type == InsightType.GROWTH],
                key=lambda x: x.created_at,
            )
            
            if growth_insights:
                # Stream existing insights in the same format
                for insight in growth_insights:
                    yield {
                        "type": "success", 
                        "data": {
                            "content": insight.content, 
                            "cached": True, 
                            "slug": insight.slug,
                            "imageUrl": insight.thumbnail_url
                        }
                    }
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
            - Make title of each insight catchy
            - Be specific about time periods and trends
            - Connect insights across different growth dimensions
            - Highlight both positive and concerning patterns
            - Support insights with relevant data points
            - Consider both quantitative and qualitative factors

            Here is the annual financial data:
            {json.dumps(annual_financial_statements_json, indent=2)}

            Here is the quarterly financial data:
            {json.dumps(quarterly_financial_statements_json, indent=2)}
        """

        insights = agent.generate_content(
            prompt=prompt,
            model_name=ModelName.GeminiFlashLite,
            stream=False,
            config={
                "response_mime_type": "application/json",
                "response_schema": list[CompanyInsight]
            }
        )

        for parsed_insight in insights:
            saved_insight = await process_parsed_streaming_insights(ticker, parsed_insight, InsightType.GROWTH)
            yield {
                "type": "success",
                "data": saved_insight
            }

    except Exception as e:
        logger.error(f"Error getting growth insights for company", {
            "ticker": ticker,
            "error": str(e)
        })
        yield {"type": "error", "content": "Error getting growth insights for company"}


async def get_earning_insights_for_ticker(ticker: str) -> AsyncGenerator[Dict[str, Any], None]:
    try:
        # First check for existing insights
        existing_insights = company_insight_connector.get_by_type(ticker, InsightType.EARNINGS)
        if existing_insights:
            # Stream existing insights in the same format
            for insight in existing_insights:
                yield {
                    "type": "success", 
                    "data": {
                        "content": insight.content, 
                        "cached": True, 
                        "slug": insight.slug,
                        "imageUrl": insight.thumbnail_url
                    }
                }
            return

        # If no existing insights, generate new ones
        annual_financial_statements_json = company_financial_connector.get_annual_income_statements(ticker)
        quarterly_financial_statements_json = company_financial_connector.get_quarterly_income_statements(ticker)

        prompt = f"""
            You are a seasoned financial analyst specializing in earnings analysis. 
            Your task is to analyze {ticker}'s earning trajectory and provide unique, actionable insights.

            Focus on these key earnings dimensions:
            1. Net Income & Profitability
               - Analyze net income trends and growth patterns. Check if they are consistent over time.
               - Examine net profit margin evolution. Check if the company is having decent profits over time.
               - Evaluate quality and sustainability of earnings. Whether the company is growing by good or bad debt. Does it reinvest its earnings for future growth.

            2. Earnings Per Share (EPS)
               - Track EPS growth and consistency
               - Analyze impact of share buybacks/dilution. Check if it is a concern when the company buybacks share to artificially boost EPS without actual earnings improvement.

            3. Operating Performance
               - Examine operating income trends
               - Analyze operating margin dynamics
               - Operating income (EBIT) and operating margin = Operating Income / Revenue.
               - Evaluate operational efficiency. Watch out for: Significant fluctuations may indicate problems with cost control or declining efficiency.

            4. Earnings Quality & Sustainability
               - Assess earnings quality and reliability
               - Identify one-time items and their impact
               - Evaluate earnings sustainability

            Guidelines for your analysis:
            - Have 4 insights in total for all the earnings dimensions. Each insight should have less than 200 words.
            - Make title of each insight catchy
            - Be specific about time periods and trends
            - Connect insights across different earnings metrics
            - Highlight both positive and concerning patterns
            - Support insights with relevant data points
            - Consider both quantitative and qualitative factors

            Here is the annual financial data:
            {json.dumps(annual_financial_statements_json, indent=2)}

            Here is the quarterly financial data:
            {json.dumps(quarterly_financial_statements_json, indent=2)}
        """

        insights = agent.generate_content(
            prompt=prompt,
            model_name=ModelName.GeminiFlashLite,
            stream=False,
            config={
                "response_mime_type": "application/json",
                "response_schema": list[CompanyInsight]
            }
        )

        for parsed_insight in insights:
            saved_insight = await process_parsed_streaming_insights(ticker, parsed_insight, InsightType.EARNINGS)
            yield {
                "type": "success",
                "data": saved_insight
            }

    except Exception as e:
        logger.error(f"Error getting earnings insights for company", {
            "ticker": ticker,
            "error": str(e)
        })
        yield {"type": "error", "content": "Error getting earnings insights for company"}

async def get_cash_flow_insights_for_ticker(ticker: str) -> AsyncGenerator[Dict[str, Any], None]:
    try:
        logger.info("Get cash flow insights for ticker", extra={"ticker": ticker})
        # First check for existing insights
        existing_insights = company_insight_connector.get_by_type(ticker, InsightType.CASH_FLOW)
        if existing_insights:
            # Stream existing insights in the same format
            for insight in existing_insights:
                yield {
                    "type": "success", 
                    "data": {
                        "content": insight.content, 
                        "cached": True, 
                        "slug": insight.slug,
                        "imageUrl": insight.thumbnail_url
                    }
                }
            return

        # If no existing insights, generate new ones
        annual_cash_flow_statements_json = company_financial_connector.get_annual_cash_flow_statements(ticker)
        quarterly_cash_flow_statements_json = company_financial_connector.get_quarterly_cash_flow_statements(ticker)

        logger.info("Found cash flow statements. Getting insights from model", {"ticker": ticker})
        prompt = f"""
            You are a seasoned financial analyst specializing in cash flow analysis. 
            Your task is to analyze {ticker}'s cash flow and provide unique, actionable insights.

            Focus on these key cash flow dimensions:
            1. Operating Cash Flow Dynamics
               - Analyze operating cash flow trends and quality
               - Evaluate working capital management
               - Assess cash conversion cycle efficiency

            2. Capital Expenditure & Investment
               - Examine capital expenditure patterns
               - Analyze investment in growth vs maintenance
               - Evaluate return on invested capital

            3. Free Cash Flow Analysis
               - Track free cash flow generation
               - Analyze free cash flow yield
               - Evaluate cash flow sustainability

            4. Financing & Capital Structure
               - Assess debt management and leverage
               - Analyze dividend and share buyback policies
               - Evaluate capital structure optimization

            Guidelines for your analysis:
            - Have 4 insights in total for all the cash flow dimensions. Each insight should have less than 200 words.
            - Make title of each insight catchy
            - Be specific about time periods and trends
            - Connect insights across different cash flow metrics
            - Highlight both positive and concerning patterns
            - Support insights with relevant data points from the provided financial data
            - Consider both quantitative and qualitative factors

            Here is the annual financial data:
            {json.dumps(annual_cash_flow_statements_json, indent=2)}

            Here is the quarterly financial data:
            {json.dumps(quarterly_cash_flow_statements_json, indent=2)}

            All metrics from the financial data have values in thousands. In the insights, use billions when possible.
        """

        insights = agent.generate_content(
            prompt=prompt,
            model_name=ModelName.GeminiFlashLite,
            stream=False,
            config={
                "response_mime_type": "application/json",
                "response_schema": list[CompanyInsight]
            }
        )

        for parsed_insight in insights:
            saved_insight = await process_parsed_streaming_insights(ticker, parsed_insight, InsightType.CASH_FLOW)
            yield {
                "type": "success",
                "data": saved_insight
            }

    except Exception as e:
        logger.error(f"Error getting cash flow insights for company", {
            "ticker": ticker,
            "error": str(e)
        })
        yield {"type": "error", "content": "Error getting cash flow insights for company"}


def get_insights_for_ticker(ticker: str, type: InsightType) -> AsyncGenerator[Dict[str, Any], None]:
    if type == InsightType.GROWTH:
        return get_growth_insights_for_ticker(ticker)
    if type == InsightType.EARNINGS:
        return get_earning_insights_for_ticker(ticker)
    if type == InsightType.CASH_FLOW:
        return get_cash_flow_insights_for_ticker(ticker)
    
    raise Exception(f"Invalid insight type: {type}")



