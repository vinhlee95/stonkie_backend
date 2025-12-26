import logging
from typing import Any, AsyncGenerator, Dict, List

from agent.agent import Agent
from agent.multi_agent import MultiAgent
from ai_models.gemini import ContentType
from ai_models.model_name import ModelName
from connectors.company_financial import CompanyFinancialConnector

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


async def analyze_financial_report(
    ticker: str, period_end_at: str, period_type: str
) -> AsyncGenerator[Dict[str, Any], None]:
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
                "content": f"No filing found for {ticker} for period {period_end_at} ({period_type})",
            }
            return

        yield {"type": "thinking_status", "body": f"Found filing for {ticker}. Starting AI analysis..."}

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

        yield {"type": "thinking_status", "body": "Generating AI analysis of the financial report..."}

        answers = ""

        for part in agent.generate_content(
            prompt=prompt,
            model_name=ModelName.Gemini25FlashLite,
            stream=True,
            thought=True,
            use_url_context=True,
        ):
            if part.type == ContentType.Thought:
                yield {"type": "thinking_status", "body": part.text}
            elif part.type == ContentType.Answer:
                answers += part.text if part.text else ""
                yield {"type": "answer", "body": part.text if part.text else "❌ No analysis generated from the model"}

        related_question_prompt = f"""
            Based on the analysis: {answers} for {ticker.upper()}, suggest exactly 3 short and insightful follow-up questions an investor might have about the company's financial health or future outlook.

            Requirements:
            - Keep follow-up questions short, less than 15 words each
            - Put EACH question on its OWN LINE
            - Do NOT number the questions or add any prefixes
        """

        related_agent = MultiAgent(model_name=ModelName.Gemini25FlashLite)
        for question in related_agent.generate_content_by_lines(
            prompt=related_question_prompt,
            use_google_search=False,
            max_lines=3,
            min_line_length=10,
            strip_numbering=True,
            strip_markdown=True,
        ):
            yield {"type": "related_question", "body": question}
    except Exception as e:
        logger.error(f"Error analyzing financial report for {ticker} ({period_end_at}): {str(e)}")
        yield {"type": "error", "content": f"Error during analysis: {str(e)}"}


async def analyze_uploaded_file(
    ticker: str, question: str, file_content: bytes, filename: str
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Service method to analyze an uploaded financial document using AI with native PDF support

    Args:
        ticker: Company ticker symbol
        question: The question to answer about the financial data
        file_content: Raw bytes of the uploaded PDF file
        filename: Name of the uploaded file

    Yields:
        Analysis results as streaming dictionary chunks
    """
    try:
        yield {"type": "thinking_status", "body": f"Processing uploaded file: {filename}..."}

        # Create analysis prompt with structured sections (PDF content will be automatically included by OpenRouter)
        prompt = f"""
            You are a seasoned financial analyst. Analyze the provided financial document for {ticker.upper()} and answer this question:

            Question: {question}

            **Instructions for your analysis:**

            Structure your response with EXACTLY 3 sections in this order:

            **Summary**
            (~60 words) Provide a concise overview that directly answers the question and previews the key findings.

            **Key Findings**
            (~120 words) Focus on:
            - Most relevant financial metrics and data points from the document
            - Trends, patterns, or changes that address the question
            - Specific numbers and percentages that support the analysis

            **Context & Implications**
            (~100 words) Focus on:
            - Business context and what the findings mean for the company
            - Risks, opportunities, or strategic considerations
            - Forward-looking insights if relevant to the question

            **Formatting Guidelines:**
            - Start each section with its title in markdown bold: **Section Title**
            - Add a blank line after the title before starting the paragraph
            - Each section should be a cohesive paragraph (or 2-3 short paragraphs)
            - Use numbers strategically - include 3-5 key figures that best support your analysis
            - Keep total response under 280 words

            **Analysis Rules:**
            - DIRECTLY ANSWER THE QUESTION: Stay focused on what was asked
            - USE DOCUMENT DATA: Reference specific information from the document provided
            - EXPLAIN REASONING: Clarify WHY the data matters and WHAT it means for the business
            - BE CONCISE: Every sentence should add value
            - NO SPECULATION: Only analyze what's in the document unless using search for context
        """

        yield {"type": "thinking_status", "body": "Analyzing document with AI..."}

        # Use MultiAgent with native PDF support - no manual text extraction needed
        analysis_agent = MultiAgent(model_name=ModelName.Gemini25FlashLite)
        full_answer = ""

        for text_chunk in analysis_agent.generate_content_with_pdf_context(
            prompt=prompt,
            pdf_content=file_content,
            filename=filename,
            pdf_engine="pdf-text",  # Fast text extraction
        ):
            full_answer += text_chunk if text_chunk else ""
            yield {"type": "answer", "body": text_chunk if text_chunk else "❌ No analysis generated from the model"}

        # Generate related questions
        yield {"type": "thinking_status", "body": "Generating follow-up questions..."}

        related_question_prompt = f"""
            Based on this analysis for {ticker.upper()}: {full_answer}

            Suggest exactly 3 short and insightful follow-up questions an investor might have about the company's financial health or future outlook.

            Requirements:
            - Keep follow-up questions short, less than 15 words each
            - Put EACH question on its OWN LINE
            - Do NOT number the questions or add any prefixes
        """

        related_agent = MultiAgent(model_name=ModelName.Gemini25FlashLite)
        for question_text in related_agent.generate_content_by_lines(
            prompt=related_question_prompt,
            use_google_search=False,
            max_lines=3,
            min_line_length=10,
            strip_numbering=True,
            strip_markdown=True,
        ):
            yield {"type": "related_question", "body": question_text}

    except Exception as e:
        logger.error(f"Error analyzing uploaded file for {ticker}: {str(e)}")
        yield {"type": "error", "content": f"Error during file analysis: {str(e)}"}
