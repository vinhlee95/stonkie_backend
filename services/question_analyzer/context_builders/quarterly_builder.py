"""Context builder for QUARTERLY_SUMMARY financial data requirement."""

from .base import ContextBuilder, ContextBuilderInput
from .components import PromptComponents


class QuarterlyContextBuilder(ContextBuilder):
    """Builds context for questions requiring quarterly report analysis."""

    def build(self, input: ContextBuilderInput) -> str:
        """Build context for quarterly financial report analysis."""
        if input.deep_analysis:
            return self._build_deep_analysis(input)
        return self._build_short_analysis(input)

    def _build_short_analysis(self, input: ContextBuilderInput) -> str:
        """Build context for short, scannable analysis (default)."""
        base_context = PromptComponents.base_context(input.ticker, input.question)

        # Extract filing URL from quarterly statement
        filing_url = None
        if input.quarterly_statements and len(input.quarterly_statements) > 0:
            filing_url = input.quarterly_statements[0].get("filing_10q_url")

        filing_context = f"Quarterly Report URL: {filing_url}" if filing_url else "No filing URL available"

        return f"""
            {base_context}
            
            {filing_context}

            **Instructions for your analysis:**

            Analyze the quarterly report available at the URL provided above and organize your findings into focused sections. 
            If the URL is accessible, use it as your ONLY source of information. Do not search for additional data sources. This is critical.

            **IMPORTANT: Section Selection**
            You must select ONLY the most important aspects that directly answer the user's question. Limit your analysis to a maximum of 3 sections. Prioritize quality and relevance over quantity - choose the 2-3 most critical insights that provide the most value.

            **Structure:**
            - Start with a brief introductory paragraph (under 80 words) that directly answers the user's question
            - Follow with 2-3 focused sections maximum, each covering the most important distinct key aspect or finding
            - Each section should have a bold, descriptive heading: **Section Heading**
            - Each section should be 50-80 words total: include key metrics and 1-2 sentences explaining the "why" behind the figures
            - Maximum 3 sections - focus on the most critical insights only

            **Section Guidelines:**
            - Select only the 2-3 most important aspects that directly answer the user's question - quality over quantity
            - Each section heading should be specific, descriptive, and informative (3-5 words max). The section headings must be in separate lines and bolded.
            - Add a new line after each section heading.
            - Each section should: (1) state key figures and metrics, (2) include 1-2 sentences explaining WHY they changed or WHAT drives them
            - Keep the "why" explanation concise - 1-2 sentences that explain the underlying reason, business driver, or implication
            - At the end of each section, provide the source information in this format: "(Section name from document on page X)"
            - Include specific numbers and metrics where relevant
            - Highlight significant changes, trends, or anomalies
            - Bold important figures and percentages
            - Prioritize the most relevant and impactful information that directly answers the user's question
            - Use the largest appropriate unit for numbers (e.g., "$1.5 billion" not "$1,500 million")
            - Make every word count - be concise but include the "why" explanation

            **Analysis Requirements:**
            - EXPLAIN THE "WHY" IN 1-2 SENTENCES: After stating key figures, briefly explain what caused changes, what drives performance, or what it means
            - Keep explanations concise but meaningful - focus on the most important driver or implication

            **Example Structure:**

            [Brief intro answering the question - under 80 words]

            **Section Heading 1**

            [Key metrics and figures (e.g., Revenue increased 15% to $50B). 1-2 sentences explaining WHY - what drove the growth, what it means, or what caused it.]

            **Section Heading 2**

            [Another key finding with metrics. 1-2 sentences explaining the "why" behind the figures.]

            **Section Heading 3** (optional - only if a third aspect is critical)

            [Third key insight with metrics. 1-2 sentences explaining the underlying reason or implication.]

            **Sources:**
            At the end, cite your sources: "Sources: Document pages X-Y" or "Sources: [Section name from document]"

            Answer in a professional, informative tone. Be concise but include 1-2 sentences per section explaining the "why" behind the figures. Prioritize clarity and scannability while providing meaningful context.
        """

    def _build_deep_analysis(self, input: ContextBuilderInput) -> str:
        """Build context for comprehensive, detailed analysis."""
        base_context = PromptComponents.base_context(input.ticker, input.question)

        # Extract filing URL from quarterly statement
        filing_url = None
        if input.quarterly_statements and len(input.quarterly_statements) > 0:
            filing_url = input.quarterly_statements[0].get("filing_10q_url")

        filing_context = f"Quarterly Report URL: {filing_url}" if filing_url else "No filing URL available"

        return f"""
            {base_context}
            
            {filing_context}

            **Instructions for your analysis:**

            Analyze the quarterly report available at the URL provided above and provide a comprehensive, detailed analysis. 
            If the URL is accessible, use it as your ONLY source of information. Do not search for additional data sources. This is critical.

            **Structure:**
            - Start with a brief introductory paragraph (under 100 words) that directly answers the user's question and provides context
            - Organize your findings into multiple focused sections, each covering a distinct key aspect or finding
            - You decide how many sections are needed to thoroughly cover the key aspects of the document that answer the user's question
            - Each section should have a bold, descriptive heading: **Section Heading**
            - Each section should be comprehensive (100-200 words) with detailed analysis, context, and implications

            **Section Guidelines:**
            - Each section heading should be specific, descriptive, and informative (3-7 words). The section headings must be in separate lines and bolded.
            - Add a new line after each section heading.
            - Each section should provide deep analysis, not just surface-level facts
            - Include specific numbers, metrics, and data points where relevant
            - Explain the significance, trends, and implications of the findings
            - Highlight significant changes, trends, or anomalies and explain WHY they matter
            - Bold important figures and percentages
            - Connect findings to business strategy, competitive position, and market dynamics
            - At the end of each section, provide the source information in this format: "(Section name from document on page X)"
            - Use the largest appropriate unit for numbers (e.g., "$1.5 billion" not "$1,500 million")
            - Prioritize analytical reasoning: explain WHY trends occur, WHAT drives changes, and WHAT it means for the business

            **Analysis Depth:**
            - PRIORITIZE REASONING: Explain WHY trends occur, WHAT drives the changes, and WHAT it means for the business
            - STRATEGIC USE OF NUMBERS: Include specific figures to support your analysis and illustrate key points
            - IDENTIFY DRIVERS: Explain the underlying business factors, market conditions, or strategic decisions behind the numbers
            - CONNECT THE DOTS: Link financial performance to business strategy, competitive position, and market dynamics
            - NO DUPLICATION: Each section should add new, distinct information
            - COMPREHENSIVE COVERAGE: Cover all important aspects that answer the user's question thoroughly

            **Example Structure:**

            [Comprehensive intro answering the question - under 100 words]

            **Section Heading 1**

            [Detailed analysis with key metrics, trends, and implications - 100-200 words]

            **Section Heading 2**

            [Another comprehensive insight with deep analysis - 100-200 words]

            [Continue with additional sections as needed to thoroughly cover all important aspects...]

            **Sources:**
            At the end, cite your sources: "Sources: Document pages X-Y" or "Sources: [Section name from document]"

            Answer in a professional, informative tone. Provide thorough, insightful analysis that demonstrates deep understanding of the document and its implications.
        """
