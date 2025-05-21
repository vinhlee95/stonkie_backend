from typing import Optional, Any
from connectors.company_insight import CompanyInsightConnector
from connectors.company_financial import CompanyFinancialConnector
from agent.agent import Agent
from enum import Enum
from dataclasses import dataclass
import json
import re

company_insight_connector = CompanyInsightConnector()
company_financial_connector = CompanyFinancialConnector()
agent = Agent(model_type="gemini")
openai_agent = Agent(model_type="openai")

class ContentType(str, Enum):
    TEXT = "text"
    CHART = "chart"

@dataclass(frozen=True)
class Content:
    type: ContentType
    content: str
    data: Optional[dict[str, Any]] = None

async def generate_dynamic_report_for_insight(ticker: str, slug: str):
    """
    Generate a dynamic report for a given insight
    """
    insight = company_insight_connector.get_by_slug(slug)
    if not insight:
        yield Content(type=ContentType.TEXT, content="Insight not found").__dict__
        return

    if insight.company_symbol != ticker:
        yield Content(type=ContentType.TEXT, content="Insight does not belong to the ticker").__dict__
        return
    
    # Fetch financial statements for the ticker
    annual_income_statements = company_financial_connector.get_annual_income_statements(ticker)
    quarterly_income_statements = company_financial_connector.get_quarterly_income_statements(ticker)

    prompt = f"""
      You are a financial analyst. Analyze the company's performance based on the following data and insight:

      FINANCIAL DATA:
      Annual Statements: {annual_income_statements}
      Quarterly Statements: {quarterly_income_statements}

      You are given the following insight:
      {insight.content}

      Generate a comprehensive report that analyzes the key metrics mentioned in the insight. Focus on the most relevant financial indicators and trends that support or explain the insight. To generate the report, use following sources:
      - Financial statements
      - Company website
      - Industry reports
      - News articles
      - Analyst reports
      - Company filings
      - Other sources

      If the data is available, make the analysis over the last 5 years or 4 quarters.

      Follow this JSON format precisely:
            {{
                "type": "text" | "chart",
                "title": "string", // This will be the title of the section
                "content": "string", // This will be the text insight or a description/title for the chart. For the chart, use 20-50 words to describe key insights from the chart.
                "data": {{...}} | null // This will be the data for the chart if type is 'chart', otherwise null
                "source": list[string] // This list all the sources of the information you used to generate the insight. This is a must and make them as precise as possible.
            }},
            ... // More content blocks
        Do not include the array brackets in the response. Start with the first object and end with the last object.

        For "type": "text", the "content" field should contain the textual insight or analysis, and "data" should be null.
            - Each text section should be 100-150 words and provide deep analysis of a specific aspect of the insight. 
            - Avoid quoting the numbers from the financial statements if possible because the numbers will be shown in the following chart sections. 
            - Focus on the analysis and future potential as well as concerns.
            - Use linebreaks to break the insights to 2-3 parts so that it is easier to read.

        For "type": "chart", the "content" field should contain a descriptive title or summary of what the chart illustrates. The "data" field should contain a JSON object suitable for generating a chart. This 'data' object should be a list of dictionaries, where each dictionary represents a data point or category. Structure this data in a way that is commonly used for charting libraries (e.g., a list of objects with keys for categories/labels and values).

        Here's an example of the 'data' structure for a simple time series chart:
        "data": [
            {{"period": "2023", "value": 100, "metric": "revenue", "value_type": "currency" or "percentage"}}, // for annual data
            {{"period": "12/31/2024", "value": 20, "metric": "revenue", "value_type": "currency" or "percentage"}}, // for quarterly data
            // ... more data points
        ]
        Make sure to strictly follow the key names: period, value, metric and data structure.
        The period should be in exactly similar format as in the financial statements.

      Guidelines:
      1. Generate at least 2 distinct insights/sections in the report
      2. Focus on metrics that are most relevant to the given insight
      3. Do not make up any numbers in the insights. Return the numbers as they are in the financial statements without any formatting or rounding.
      4. Be smart about when to use charts versus text – use charts to show trends or comparisons that are hard to convey in text alone
      5. Ideally, each "chart" type should follow a "text" type to illustrate the insights
      6. For "chart" type, separate annual and quarterly data into separate charts when appropriate

      Only return the data in the JSON format. Do not include any other text or comments.
    """
    response = openai_agent.generate_content(prompt=prompt, stream=True)
    current_text = ""
    async for chunk in response:
        if isinstance(chunk, str):
            current_text += chunk
            
            # Look for complete JSON objects
            while True:
                # Find the start of a JSON object (first '{')
                start_idx = current_text.find('{')
                if start_idx == -1:
                    break
                
                # Try to find a complete JSON object
                json_str = ""
                brace_count = 0
                for i in range(start_idx, len(current_text)):
                    char = current_text[i]
                    json_str += char
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            # Found a complete JSON object
                            try:
                                json_obj = json.loads(json_str)
                                if isinstance(json_obj, dict) and 'type' in json_obj:
                                    yield json_obj
                                    # Remove the parsed object from current_text
                                    current_text = current_text[i+1:].strip()
                                    break
                            except json.JSONDecodeError:
                                # If parsing fails, continue looking
                                pass
                
                # If we didn't find a complete valid JSON object, wait for more chunks
                if brace_count != 0:
                    break
        else:
            print(f"Received non-string chunk: {chunk}")  # Debug: show non-string chunks

async def generate_detailed_report_for_insight(ticker: str, slug: str):
    """
    Generate a report for a given insight already persisted in the database
    """
    insight = company_insight_connector.get_by_slug(slug)
    if not insight:
        yield {"type": "error", "content": "Insight not found"}
        return

    if insight.company_symbol != ticker:
        yield {"type": "error", "content": "Insight does not belong to the ticker"}
        return
      
    insight_type = insight.insight_type
    # TODO: support other insight types
    if insight_type != "growth":
        yield {"type": "error", "content": "Only growth insights are supported for now"}
        return
    
    # Fetch financial statements for the ticker
    annual_income_statements = company_financial_connector.get_annual_income_statements(ticker)
    quarterly_income_statements = company_financial_connector.get_quarterly_income_statements(ticker)
    
    prompt = f"""
      You are a financial analyst.
      You are in the middle of writing a detailed report for a company's growth insight.
      Your starting point is the following insight: 
      {insight.content}

      You are given the following financial statements:
      {annual_income_statements}
      {quarterly_income_statements}

      The report should start from the first section of the report. No need for any background information.
      No need to start with "Here's a financial analysis report based on the provided information:".

      Focus on following dimensions. Each dimension should be a title having a separate section in the report. 
      Make sure the title is bold in markdown.

      - Annual revenue growth
      - Quarterly revenue growth
        - Analyze growth patterns, seasonality, and acceleration/deceleration
      - Identify growth drivers and their sustainability and future growth potential
      - Analyze risks to growth sustainability

      
      Continue to generate more insights given the above information.
      Make sure to include the data points and numbers to support the insight.
      Do not make up any numbers that are not present in the financial statements.
      Feel free to include general knowledge about the company and the industry.
      Provide the report in markdown format.
      Make the report in less than 750 words.

      At the end of the report, name the sources of the information you used to generate the report.
    """
    
    response = await agent.generate_content(prompt=prompt, stream=True)
    async for chunk in response:
        yield chunk.text
    