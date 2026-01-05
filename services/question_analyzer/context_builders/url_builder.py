"""Context builder for URL_CONTEXT financial data requirement."""

from .base import ContextBuilder, ContextBuilderInput
from .components import PromptComponents


class UrlContextBuilder(ContextBuilder):
    """Builds context for questions requiring URL-based document analysis."""

    def build(self, input: ContextBuilderInput) -> str:
        """Build context for URL-based document analysis."""
        if input.deep_analysis:
            return self._build_deep_analysis(input)
        return self._build_short_analysis(input)

    def _build_short_analysis(self, input: ContextBuilderInput) -> str:
        """Build context for short, scannable analysis (default)."""
        base_context = PromptComponents.base_context(input.ticker, input.question)

        # Extract URL from input
        source_url = input.source_url or "No source URL available"
        url_context = f"Source Document URL: {source_url}"

        section_structure = PromptComponents.section_structure_template()
        example_structure = PromptComponents.example_structure()

        return f"""
            {base_context}
            
            {url_context}

            **Instructions for your analysis:**

            Analyze the document available at the URL provided above and organize your findings into multiple focused sections. 
            If the URL is accessible, use it as your ONLY source of information. Do not search for additional data sources. This is critical.

            You decide how many sections are needed to thoroughly cover the key aspects of the document that answer the user's question.

            {section_structure}

            {example_structure}

            **Sources:**
            At the end, cite your sources: "Sources: Document pages X-Y" or "Sources: [Section name from document]"

            {PromptComponents.source_instructions()}

            Answer in a professional, informative tone. Prioritize clarity and scannability over narrative flow.
        """

    def _build_deep_analysis(self, input: ContextBuilderInput) -> str:
        """Build context for comprehensive, detailed analysis."""
        base_context = PromptComponents.base_context(input.ticker, input.question)

        # Extract URL from input
        source_url = input.source_url or "No source URL available"
        url_context = f"Source Document URL: {source_url}"

        # Word allocation: 80 words for summary, 160 words each for 2 main sections (total: 400)
        summary_words = 80
        section_words = 160

        return f"""
            {base_context}
            
            {url_context}

            **Instructions for your analysis:**

            Analyze the document available at the URL provided above. If the URL is accessible, use it as your ONLY source of information. Do not search for additional data sources. This is critical.

            Structure your response with EXACTLY 3 sections in this order:

            **Summary**

            (~{summary_words} words) Provide a concise overview that directly answers the user's question and previews the key findings from the two sections below. Highlight the most important takeaway.

            **Key Financial Highlights**

            (~{section_words} words) Focus on:
            - Revenue, profit margins, growth rates and trends over the years mentioned in the report
            - Indicate if performance is strong, weak, or mixed
            - Include specific numbers and percentages where available

            **Business Operations & Risk Factors**

            (~{section_words} words) Focus on:
            - Key business developments and operational metrics in the report
            - Assess if operations are improving, declining, or stable
            - Notable risks, challenges, competitive landscape mentioned in the report
            - Evaluate if these are manageable or concerning

            **Formatting Guidelines:**
            - Start each section with its title in markdown bold: **Section Title**
            - Add a blank line after the title before starting the paragraph
            - Each section should be a cohesive paragraph (or 2-3 short paragraphs)
            - Use numbers strategically - select 2-4 key figures per section that best support your analysis
            - Use the largest appropriate unit for numbers (e.g., "$1.5 billion" not "$1,500 million", "5.2%" not "520 basis points")
            - Keep total response under 400 words

            **Analysis Rules:**
            - PRIORITIZE REASONING: Explain WHY trends occur, WHAT drives the changes, and WHAT it means for the business
            - STRATEGIC USE OF NUMBERS: Include specific figures only when they strengthen your argument or illustrate a key point
            - IDENTIFY DRIVERS: Explain the underlying business factors, market conditions, or strategic decisions behind the numbers
            - CONNECT THE DOTS: Link financial performance to business strategy, competitive position, and market dynamics
            - NO DUPLICATION: Each sentence should add new information
            - Only reference information from the document at the provided URL

            {PromptComponents.source_instructions()}
        """
