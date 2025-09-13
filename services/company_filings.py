from typing import List, Dict, Any, AsyncGenerator
from connectors.company_financial import CompanyFinancialConnector
from agent.agent import Agent
from ai_models.model_name import ModelName
import logging
from ai_models.gemini import ContentType

logger = logging.getLogger(__name__)
company_financial_connector = CompanyFinancialConnector()
agent = Agent(model_type="gemini")

def get_company_filings(ticker: str, period: str) -> List[Dict[str, Any]]:
    """
    Service method to get company filings for a given ticker and period
    
    Args:
        ticker: Company ticker symbol
        period: Period type ('annual' or 'quarterly')
    
    Returns:
        List of filing objects with url and period_end_year
    """
    return company_financial_connector.get_company_filings(ticker, period)

async def analyze_financial_report(ticker: str, period_end_at: str, period_type: str) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Service method to analyze a company's financial report using AI
    
    Args:
        ticker: Company ticker symbol
        period_end_at: End date of the period (e.g., '2023' for annual, '6/30/2023' for quarterly)
        period_type: Type of the period ('annually' or 'quarterly')
    
    Yields:
        Analysis results as streaming dictionary chunks
    """
    try:
        # Fetch the report URL based on the ticker and period
        company_filing_url = company_financial_connector.get_company_filing_url(ticker, period_end_at, period_type)
        
        if not company_filing_url:
            yield {
                "type": "error",
                "content": f"No filing found for {ticker} for period {period_end_at} ({period_type})"
            }
            return
        
        yield {
            "type": "status",
            "content": f"Found filing for {ticker}. Starting AI analysis..."
        }
        
        # Create analysis prompt
        prompt = f"""
        Analyze the financial report for {ticker.upper()} for the period ending {period_end_at} ({period_type}).
        
        Report URL: {company_filing_url}
        
        Please provide a comprehensive analysis covering:
        1. Key Financial Highlights - Revenue, profit margins, cash flow
        2. Year-over-Year Performance - Growth rates and trends
        3. Financial Health - Debt levels, liquidity, financial ratios
        4. Business Operations - Key business developments and operational metrics
        5. Risk Factors - Any notable risks or challenges mentioned
        6. Future Outlook - Management guidance and forward-looking statements
        
        Format the response in clear sections with bullet points for readability.
        Include specific numbers and percentages where available.
        Keep the analysis comprehensive but concise. Keep the whole response under 500 words.
        """
        
        yield {
            "type": "status", 
            "content": "Generating AI analysis of the financial report..."
        }
        
        for part in agent.generate_content(
            prompt=prompt, 
            model_name=ModelName.Gemini25FlashLite,
            stream=True, 
            thought=True,
        ):
            if part.type == ContentType.Thought:
                yield {
                    "type": "thinking_status",
                    "body": part.text
                }
            elif part.type == ContentType.Answer:
                yield {
                    "type": "answer",
                    "body": part.text if part.text else "❌ No analysis generated from the model"
                }
        
        yield {
            "type": "status",
            "content": "Analysis complete."
        }
        
    except Exception as e:
        logger.error(f"Error analyzing financial report for {ticker} ({period_end_at}): {str(e)}")
        yield {
            "type": "error",
            "content": f"Error during analysis: {str(e)}"
        }
        