from connectors.company_insight import CompanyInsightConnector
from connectors.company_financial import CompanyFinancialConnector
from agent.agent import Agent

company_insight_connector = CompanyInsightConnector()
company_financial_connector = CompanyFinancialConnector()
agent = Agent(model_type="gemini")

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
    