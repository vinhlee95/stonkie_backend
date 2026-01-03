"""Context builder for QUARTERLY_SUMMARY financial data requirement."""

from .base import ContextBuilder, ContextBuilderInput
from .components import PromptComponents


class QuarterlyContextBuilder(ContextBuilder):
    """Builds context for questions requiring quarterly report analysis."""

    def build(self, input: ContextBuilderInput) -> str:
        """Build context for quarterly financial report analysis."""
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

            Analyze the quarterly report available at the URL provided above and organize your findings into multiple focused sections. 
            If the URL is accessible, use it as your ONLY source of information. Do not search for additional data sources. This is critical.

            You decide how many sections are needed to thoroughly cover the key aspects of the document that answer the user's question.

            **Structure:**
            - Start with a brief introductory paragraph (under 50 words) that directly answers the user's question
            - Follow with multiple focused sections, each covering a distinct key aspect or finding
            - Each section should have a bold, descriptive heading: **Section Heading**
            - Keep each section content under 30 words - be concise and to the point
            - Typical number of sections: 2-5 depending on document complexity and question scope

            **Section Guidelines:**
            - Each section heading should be specific, descriptive, and catchy (3-5 words max). The section headings must be in separate lines and bolded.
            - Add a new line after each section heading.
            - Each section content should focus on ONE key finding or aspect
            - At the end of each section, provide the source information in this format: "(Section name from document on page X)"
            - Include specific numbers and metrics where relevant
            - Highlight significant changes, trends, or anomalies
            - Bold important figures and percentages
            - Prioritize the most relevant information that directly answers the user's question
            - Use the largest appropriate unit for numbers (e.g., "$1.5 billion" not "$1,500 million")
            - Make every word count - avoid filler phrases

            **Example Structure:**

            [Brief intro answering the question - under 50 words]

            **Section Heading 1**

            [Concise finding with key metrics - under 50 words]

            **Section Heading 2**

            [Another focused insight - under 50 words]

            [Continue with additional sections as needed...]

            **Sources:**
            At the end, cite your sources: "Sources: Document pages X-Y" or "Sources: [Section name from document]"

            Answer in a professional, informative tone. Prioritize clarity and scannability over narrative flow.
        """
