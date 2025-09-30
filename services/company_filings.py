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
            "type": "thinking_status",
            "body": f"Found filing for {ticker}. Starting AI analysis..."
        }
        
        # Create analysis prompt
        prompt = f"""
            Analyze the financial report for {ticker.upper()} available at this URL: {company_filing_url}.

            Please provide a comprehensive analysis covering:
            - Key Financial Highlights - Revenue, profit margins, growth rates and trends over the years mentioned in the report. Indicate if performance is strong, weak, or mixed.
            - Business Operations - Key business developments and operational metrics in the report. Assess if operations are improving, declining, or stable.
            - Risk Factors - notable risks, challenges, competitive landscape mentioned in the report. Evaluate if these are manageable or concerning.

            Structure the analysis in clear sections and bullet points where appropriate for better readability.
            The first paragraph should be a concise summary, in under 50 words, of the overall financial health and performance of the company based on the report.
            Include specific numbers and percentages where available.
            Only reference information from the report.
            Keep the analysis comprehensive but concise. Keep the whole analysis UNDER 120 words. Do not mention any word count or length in the analysis.
        """
        
        yield {
            "type": "thinking_status", 
            "body": "Generating AI analysis of the financial report..."
        }

        answers = ""
        
        for part in agent.generate_content(
            prompt=prompt, 
            model_name=ModelName.Gemini25FlashLite,
            stream=True, 
            thought=True,
            use_url_context=True,
        ):
            if part.type == ContentType.Thought:
                yield {
                    "type": "thinking_status",
                    "body": part.text
                }
            elif part.type == ContentType.Answer:
                answers += part.text if part.text else ""
                yield {
                    "type": "answer",
                    "body": part.text if part.text else "❌ No analysis generated from the model"
                }
        
        related_question_prompt = f"""
            Based on the analysis: {answers} for {ticker.upper()}, 
            suggest 3 short and insightful follow-up questions an investor might have about the company's financial health or future outlook.
            Make sure that follow-up questions are short, less than 15 words each.
            Return only the questions, do not return the number or order of the question.
        """
        response = agent.generate_content_and_normalize_results(related_question_prompt, model_name=ModelName.Gemini25FlashLite)
        async for question in response:
            yield {
                "type": "related_question",
                "body": question
            }
    except Exception as e:
        logger.error(f"Error analyzing financial report for {ticker} ({period_end_at}): {str(e)}")
        yield {
            "type": "error",
            "content": f"Error during analysis: {str(e)}"
        }
        