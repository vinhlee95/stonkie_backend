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
      You are a financial analyst. Analyze the revenue trends from the following data:

      FINANCIAL DATA:
      Annual Statements: {annual_income_statements}
      Quarterly Statements: {quarterly_income_statements}

      You are given the following insight:
      {insight.content}

      Generate a report for the insight.

      Follow this JSON format precisely:
        [
            {{
                "type": "text" | "chart",
                "content": "string", // This will be the text insight or a description/title for the chart
                "data": {{...}} | null // This will be the data for the chart if type is 'chart', otherwise null
            }},
            ... // More content blocks
        ]

        For "type": "text", the "content" field should contain the textual insight or analysis, and "data" should be null.

        For "type": "chart", the "content" field should contain a descriptive title or summary of what the chart illustrates. The "data" field should contain a JSON object suitable for generating a chart. This 'data' object should be a list of dictionaries, where each dictionary represents a data point or category. Structure this data in a way that is commonly used for charting libraries (e.g., a list of objects with keys for categories/labels and values).

        Here's an example of the 'data' structure for a simple time series chart:
        "data": [
            {{"period": "2023-01", "value": 100}},
            {{"period": "2023-02", "value": 120}},
            {{"period": "2023-03", "value": 110}},
            // ... more data points
        ]

      Provide insights in the described JSON format. Be smart about when to use charts versus text – use charts to show trends or comparisons that are hard to convey in text alone, and use text to explain the charts, provide context, or offer other observations.
    """

    try:
      response = await agent.generate_content(prompt=prompt, stream=True)
      buffer = ""
      
      async for chunk in response:
          try:
              # Clean the chunk and add to buffer
              cleaned_chunk = chunk.text.replace('```json', '').replace('```', '').strip()
              buffer += cleaned_chunk
              
              # Look for complete JSON objects in the buffer
              while True:
                  # Find the first complete JSON object
                  match = re.search(r'\{.*?\}', buffer, re.DOTALL)
                  if not match:
                      break
                      
                  json_str = match.group(0)
                  try:
                      # Try to parse the JSON
                      content_data = json.loads(json_str)
                      
                      # Validate the required fields
                      if not all(key in content_data for key in ['type', 'content']):
                          raise ValueError("Missing required fields in JSON")
                      
                      # Yield the content
                      yield Content(
                          type=ContentType[content_data.get("type", "TEXT").upper()],
                          content=content_data.get("content", ""),
                          data=content_data.get("data")
                      ).__dict__
                      
                      # Remove the processed JSON from the buffer
                      buffer = buffer[match.end():].strip()
                  except (json.JSONDecodeError, ValueError) as e:
                      # If parsing fails, try to find the next JSON object
                      buffer = buffer[match.start() + 1:].strip()
                      continue
                      
          except Exception as e:
              print(f"Error processing chunk: {str(e)}")
              yield Content(
                  type=ContentType.TEXT,
                  content=f"Error processing content: {str(e)}"
              ).__dict__
              buffer = ""  # Reset buffer on error
      
      # Handle any remaining content in the buffer
      if buffer.strip():
          try:
              # Try to parse any remaining content as JSON
              content_data = json.loads(buffer)
              yield Content(
                  type=ContentType[content_data.get("type", "TEXT").upper()],
                  content=content_data.get("content", ""),
                  data=content_data.get("data")
              ).__dict__
          except (json.JSONDecodeError, ValueError):
              # If it's not valid JSON, yield it as text
              yield Content(
                  type=ContentType.TEXT,
                  content=buffer
              ).__dict__
    except Exception as e:
        yield Content(
            type=ContentType.TEXT,
            content=f"Error generating report: {str(e)}"
        ).__dict__

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
    