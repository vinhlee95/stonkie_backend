import logging
import re
from typing import Optional

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from connectors.etf_fundamental import ETFFundamentalConnector

logger = logging.getLogger(__name__)


class ETFTickerExtractor:
    """Extract 2-4 ETF tickers from comparison questions using hybrid approach."""

    def __init__(self):
        self.connector = ETFFundamentalConnector()
        self.agent = MultiAgent(model_name=ModelName.Gemini30Flash)

    async def extract_tickers(self, question: str) -> list[str]:
        """
        Extract ETF tickers using two-stage approach:
        1. Regex fast path for explicit tickers
        2. AI fallback for ETF names

        Args:
            question: User question potentially containing ETF tickers/names

        Returns:
            List of 2-4 validated ETF ticker strings, or empty list
        """
        # Stage 1: Try regex extraction (fast, free)
        regex_tickers = self._extract_via_regex(question)
        if len(regex_tickers) >= 2:
            logger.info(f"Extracted {len(regex_tickers)} tickers via regex: {regex_tickers}")
            return regex_tickers

        # Stage 2: Fall back to AI extraction (handles names)
        logger.info("Regex found < 2 tickers, falling back to AI extraction")
        ai_tickers = await self._extract_via_ai(question)
        if len(ai_tickers) >= 2:
            logger.info(f"Extracted {len(ai_tickers)} tickers via AI: {ai_tickers}")
            return ai_tickers

        logger.info("No comparison detected (< 2 valid tickers)")
        return []

    def _extract_via_regex(self, question: str) -> list[str]:
        """
        Extract explicit tickers via regex pattern matching.

        Pattern: 2-6 uppercase letters/digits (ETF ticker format)
        Examples: SXR8, CSPX, VUSA, IWDA, SPYY

        Returns:
            List of validated tickers found in question
        """
        # Find potential tickers: 2-6 consecutive uppercase letters and digits
        pattern = r"\b[A-Z][A-Z0-9]{1,5}\b"
        potential_tickers = re.findall(pattern, question)

        # Validate against database
        valid_tickers = []
        for ticker in potential_tickers:
            etf = self.connector.get_by_ticker(ticker)
            if etf:
                valid_tickers.append(ticker)

        # Return 2-4 tickers (comparison range)
        return valid_tickers[:4] if len(valid_tickers) >= 2 else []

    async def _extract_via_ai(self, question: str) -> list[str]:
        """
        Extract tickers using AI to handle ETF names.

        Handles cases like:
        - "Compare iShares Core S&P 500 to Vanguard S&P 500"
        - "How does the popular MSCI World ETF compare to..."
        - Mixed: "Compare SXR8 to Vanguard S&P 500"

        Returns:
            List of validated tickers resolved from names/tickers
        """
        prompt = f"""Extract ETF tickers or names from this question.

Question: "{question}"

Return a JSON object with a "tickers" array containing 2-4 ETF identifiers (tickers or names).

Examples:
- "Compare SXR8 vs CSPX" → {{"tickers": ["SXR8", "CSPX"]}}
- "iShares S&P 500 vs Vanguard S&P 500" → {{"tickers": ["iShares S&P 500", "Vanguard S&P 500"]}}
- "Compare SXR8 to Vanguard MSCI World" → {{"tickers": ["SXR8", "Vanguard MSCI World"]}}

Return only the JSON object, no other text."""

        try:
            response = ""
            for chunk in self.agent.generate_content(prompt):
                response += chunk

            # Parse JSON response
            import json

            response = response.strip()
            # Remove markdown code blocks if present
            if response.startswith("```"):
                response = response.split("```")[1]
                if response.startswith("json"):
                    response = response[4:]
                response = response.strip()

            data = json.loads(response)
            identifiers = data.get("tickers", [])

            # Resolve each identifier to ticker
            resolved_tickers = []
            for identifier in identifiers:
                ticker = self._resolve_identifier(identifier)
                if ticker:
                    resolved_tickers.append(ticker)

            # Return 2-4 tickers
            return resolved_tickers[:4] if len(resolved_tickers) >= 2 else []

        except Exception as e:
            logger.error(f"AI extraction failed: {e}")
            return []

    def _resolve_identifier(self, identifier: str) -> Optional[str]:
        """
        Resolve an identifier (ticker or name) to a valid ticker.

        Args:
            identifier: ETF ticker (e.g., "SXR8") or name (e.g., "iShares S&P 500")

        Returns:
            Valid ticker if found, None otherwise
        """
        identifier_upper = identifier.strip().upper()
        identifier_lower = identifier.strip().lower()

        # Try as direct ticker lookup
        etf = self.connector.get_by_ticker(identifier_upper)
        if etf:
            return identifier_upper

        # Try name matching (flexible partial matching)
        all_etfs = self.connector.get_all()

        # Stage 1: Exact substring match
        for etf in all_etfs:
            if not etf.name:
                continue
            etf_name_lower = etf.name.lower()

            # Check if identifier is substring of ETF name
            if identifier_lower in etf_name_lower:
                logger.info(f"Matched '{identifier}' to '{etf.name}' ({etf.ticker})")
                return etf.ticker

            # Check if ETF name is substring of identifier
            if etf_name_lower in identifier_lower:
                logger.info(f"Matched '{identifier}' to '{etf.name}' ({etf.ticker})")
                return etf.ticker

        # Stage 2: Token-based matching (for partial names like "SPDR MSCI World")
        # Extract significant tokens from identifier (ignore common words)
        common_words = {"etf", "ucits", "usd", "acc", "dist", "the", "a", "an"}
        identifier_tokens = set(
            token for token in identifier_lower.split() if token not in common_words and len(token) > 2
        )

        best_match = None
        best_score = 0

        for etf in all_etfs:
            if not etf.name:
                continue

            etf_tokens = set(
                token for token in etf.name.lower().split() if token not in common_words and len(token) > 2
            )

            # Count matching tokens
            matching_tokens = identifier_tokens & etf_tokens
            if len(matching_tokens) > 0:
                score = len(matching_tokens) / len(identifier_tokens)  # Percentage of tokens matched
                if score > best_score and score >= 0.5:  # Require at least 50% token match
                    best_score = score
                    best_match = etf

        if best_match:
            logger.info(
                f"Fuzzy matched '{identifier}' to '{best_match.name}' ({best_match.ticker}) with score {best_score:.2f}"
            )
            return best_match.ticker

        logger.warning(f"Could not resolve identifier: {identifier}")
        return None
