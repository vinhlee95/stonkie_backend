import json
from logging import getLogger
from typing import AsyncGenerator

from agent.agent import Agent
from connectors.company_financial import CompanyFinancialConnector

logger = getLogger(__name__)

company_financial_connector = CompanyFinancialConnector()


async def get_filtered_financial_data(ticker: str, filter_type: str):
    """Fetch and filter financial data from the database."""
    try:
        financial_data = company_financial_connector.get_company_revenue_data(ticker).all()
        if not financial_data:
            return None

        # Transform SQLAlchemy objects into dictionaries
        return [
            {
                "year": data.year,
                "revenue_breakdown": [item for item in data.revenue_breakdown if item.get("type") == filter_type],
            }
            for data in financial_data
        ]
    except Exception as e:
        logger.error("Error fetching financial data", {"ticker": ticker, "error": str(e)})
        return None


async def process_streaming_insights(response) -> AsyncGenerator[dict, None]:
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
                yield {"type": "success", "data": {"content": insight_text}}

                # Remove processed insight from accumulated text
                accumulated_text = accumulated_text[end_idx + len("---INSIGHT_END---") :]
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
        if current_insight.strip() and not any(
            marker in current_insight for marker in ["---INSIGHT_START---", "---INSIGHT_END---", "---COMPLETE---"]
        ):
            yield {"type": "stream", "content": current_insight.strip()}
            current_insight = ""

        # Check if we're done
        if "---COMPLETE---" in accumulated_text:
            break


async def get_revenue_insights_for_company_product(ticker: str):
    try:
        financial_data_list = await get_filtered_financial_data(ticker, "product")
        if not financial_data_list:
            yield {"type": "error", "content": "No revenue data found for that company"}
            return

        agent = Agent(model_type="gemini")
        prompt = f"""
            You are a financial analyst tasked with analyzing revenue data for {ticker}. The data shows revenue breakdowns by product over multiple years.
            Generate 5 insights for {ticker} based on the revenue data.
            
            For each insight, apart from raw numbers taken from the data, provide an overview about the trend
            based on your own knowledge about the product and services of that company. 
            For each product and service, explain why there are increasing or declining trend. Feel free to use general knowledge or news for this.
            Each insight has around 100 words.

            For each data point:
            - Revenue numbers are in thousands of USD. If the number is billion, just mention billion in the output instead of thousands.
            - Each breakdown item has revenue and percentage values

            Be specific and data-driven:
            - Use exact numbers and percentages
            - Reference specific years and time periods
            - Highlight significant changes with data points

            The first insight MUST be a general overview covering:
            - which product is the biggest source of revenue
            - how big of a share it accounts for
            - if there is consistent growth/decline
            - any shifts in revenue mix
            - no need to points out specific number or percentage in the first insight. Focus on general trend and observation.

            Here is the revenue data:
            {json.dumps(financial_data_list, indent=2)}

            Format your response as follows:
            1. Start each insight with "---INSIGHT_START---"
            2. End each insight with "---INSIGHT_END---"
            3. Make each insight self-contained and complete
            4. End the entire response with "---COMPLETE---"

            Generate insights one at a time, ensuring each is thorough and valuable. 
            At the end of each insight, specify the source of the insight. Specify the time period of the source.
            Do not include any other text or formatting outside of these markers.
        """

        response = await agent.generate_content(prompt=prompt, stream=True)
        async for insight in process_streaming_insights(response):
            yield insight

    except Exception as e:
        logger.error("Error getting revenue insights for company", {"ticker": ticker, "error": str(e)})
        yield {"type": "error", "content": str(e)}


async def get_revenue_insights_for_company_region(ticker: str):
    try:
        financial_data_list = await get_filtered_financial_data(ticker, "region")
        if not financial_data_list:
            yield {"type": "error", "content": "No revenue data found for that company"}
            return

        agent = Agent(model_type="gemini")
        prompt = f"""
            You are a financial analyst tasked with analyzing revenue data for {ticker}. The data shows revenue breakdowns by region over multiple years.
            Generate 5 insights for {ticker} based on the revenue data.
            
            For each insight, apart from raw numbers taken from the data, provide an overview about the trend
            based on your own knowledge about the region that company is operating in.
            For each region, explain why there are increasing or declining trend. Feel free to use general knowledge or news for this.
            Each insight has around 100 words.

            For each data point:
            - Revenue numbers are in thousands of USD. If the number is billion, just mention billion in the output instead of thousands.
            - Each breakdown item has revenue and percentage values

            Be specific and data-driven:
            - Use exact numbers and percentages
            - Reference specific years and time periods
            - Highlight significant changes with data points

            The first insight MUST be a general overview covering:
            - which region is the biggest source of revenue
            - how big of a share it accounts for
            - if there is consistent growth/decline
            - any shifts in revenue mix
            - no need to points out specific number or percentage in the first insight. Focus on general trend and observation.

            Here is the revenue data:
            {json.dumps(financial_data_list, indent=2)}

            Format your response as follows:
            1. Start each insight with "---INSIGHT_START---"
            2. End each insight with "---INSIGHT_END---"
            3. Make each insight self-contained and complete
            4. End the entire response with "---COMPLETE---"

            Generate insights one at a time, ensuring each is thorough and valuable.
            At the end of each insight, specify the source of the insight. Specify the time period of the source.
            Do not include any other text or formatting outside of these markers.
        """

        response = await agent.generate_content(prompt=prompt, stream=True)
        async for insight in process_streaming_insights(response):
            yield insight

    except Exception as e:
        logger.error("Error getting revenue insights for company", {"ticker": ticker, "error": str(e)})
        yield {"type": "error", "content": str(e)}
