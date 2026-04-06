"""Extract stock tickers from comparison questions."""

import json
import logging
import re
import time
from typing import Optional

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from connectors.company import CompanyConnector

logger = logging.getLogger(__name__)

# Common English words that are also valid tickers — exclude from regex extraction
TICKER_STOPWORDS = {
    "IT",
    "A",
    "ALL",
    "ARE",
    "IS",
    "AN",
    "BE",
    "DO",
    "SO",
    "OR",
    "VS",
    "THE",
    "FOR",
    "AT",
    "ON",
    "IN",
    "TO",
    "BY",
    "UP",
    "GO",
    "AI",
    "HAS",
    "NOW",
    "CAN",
    "ANY",
    "HE",
    "HIS",
    "HER",
    "ITS",
    "OUR",
    "OUT",
    "OLD",
    "NEW",
    "BIG",
    "LOW",
    "TWO",
    "ONE",
    "WAR",
    "KEY",
    "MAN",
    "OWN",
    "RUN",
}


class StockTickerExtractor:
    """Extract 2-4 stock tickers from comparison questions using hybrid approach."""

    def __init__(self):
        self.connector = CompanyConnector()
        self.agent = MultiAgent(model_name=ModelName.Sonnet46)

    async def _preprocess_question_with_context(self, question: str, current_ticker: str) -> str:
        """
        Use AI to resolve contextual references in the question.

        Handles: "this stock", "this company", "this one", "it" → current ticker
        """
        prompt = f"""You are helping resolve contextual references in stock comparison questions.

Current context: User is viewing stock ticker "{current_ticker}"

Original question: "{question}"

Rewrite the question to replace contextual references like "this stock", "this company", "this one", "it", "that", "the current one", "the one I'm viewing" with the explicit ticker "{current_ticker}".

Rules:
- Only replace references clearly pointing to the current stock
- Keep all other tickers unchanged
- Preserve question structure and intent
- If no contextual references exist, return original question unchanged

Examples:
Input: "Compare this stock with MSFT"
Output: "Compare {current_ticker} with MSFT"

Input: "How does it compare to GOOGL?"
Output: "How does {current_ticker} compare to GOOGL?"

Input: "Compare AAPL vs MSFT"
Output: "Compare AAPL vs MSFT"

Return ONLY the rewritten question, no explanation."""

        try:
            response = ""
            for chunk in self.agent.generate_content(prompt):
                response += chunk

            rewritten = response.strip()
            return rewritten if rewritten else question

        except Exception as e:
            logger.error(f"AI preprocessing failed: {e}")
            return question

    async def extract_tickers(self, question: str, current_ticker: Optional[str] = None) -> list[str]:
        """
        Extract stock tickers using two-stage approach:
        1. Regex fast path for explicit tickers
        2. AI fallback for company names

        Returns:
            List of 2-4 validated stock ticker strings, or empty list
        """
        t_start = time.perf_counter()
        try:
            # AI preprocessing if context provided
            processed_question = question
            if current_ticker:
                processed_question = await self._preprocess_question_with_context(question, current_ticker)
                if processed_question != question:
                    logger.info(f"Context resolved: '{question}' → '{processed_question}'")

            # Stage 1: Try regex extraction (fast, free)
            regex_tickers = self._extract_via_regex(processed_question)
            if len(regex_tickers) >= 2:
                logger.info(f"[ticker_extractor] Stage 1 (regex): extracted {regex_tickers}")
                return regex_tickers

            logger.info(f"[ticker_extractor] Stage 1 (regex): found {regex_tickers}, need 2+ — falling back to AI")

            # Stage 2: Fall back to AI extraction (handles company names)
            ai_tickers = await self._extract_via_ai(processed_question)
            if len(ai_tickers) >= 2:
                logger.info(f"[ticker_extractor] Stage 2 (AI): extracted {ai_tickers}")
                return ai_tickers

            logger.info(f"[ticker_extractor] Stage 2 (AI): found {ai_tickers} — no comparison detected (< 2 tickers)")
            return []
        finally:
            logger.info(
                "Profiling StockTickerExtractor.extract_tickers: %.4fs",
                time.perf_counter() - t_start,
            )

    def _extract_via_regex(self, question: str) -> list[str]:
        """
        Extract explicit tickers via regex pattern matching.

        Pattern: 1-5 uppercase letters/digits (stock ticker format)
        Validates against database. Filters stopwords.

        Returns:
            List of validated tickers found in question
        """
        pattern = r"\b[A-Z][A-Z0-9]{0,4}\b"
        potential_tickers = re.findall(pattern, question)

        # Filter stopwords and validate against database
        valid_tickers = []
        seen = set()
        for ticker in potential_tickers:
            if ticker in TICKER_STOPWORDS or ticker in seen:
                continue
            seen.add(ticker)
            company = self.connector.get_by_ticker(ticker)
            if company:
                valid_tickers.append(ticker)

        # Return 2-4 tickers (comparison range)
        return valid_tickers[:4] if len(valid_tickers) >= 2 else []

    async def _extract_via_ai(self, question: str) -> list[str]:
        """
        Extract tickers using AI to handle company names.

        Handles cases like:
        - "Compare Apple to Microsoft"
        - "How does Nvidia compare to AMD?"
        - Mixed: "Compare AAPL to Microsoft"
        """
        prompt = f"""Extract stock ticker symbols from this comparison question.

Question: "{question}"

Return a JSON object with a "tickers" array of 2-4 stock ticker symbols.
- Prefer the official ticker symbol (e.g. "AAPL" not "Apple", "MSFT" not "Microsoft")
- For non-US companies, use their most widely known ticker (e.g. "SSNLF" for Samsung, "XIACY" for Xiaomi)
- Only extract if the question is comparing companies — otherwise return empty array
- Return only the JSON object, no other text

Examples:
- "Compare AAPL vs MSFT" → {{"tickers": ["AAPL", "MSFT"]}}
- "Apple vs Microsoft margins" → {{"tickers": ["AAPL", "MSFT"]}}
- "Compare Apple and Samsung profit" → {{"tickers": ["AAPL", "SSNLF"]}}
- "compare apple vs xiaomi" → {{"tickers": ["AAPL", "XIACY"]}}
- "What is Apple's revenue?" → {{"tickers": []}}"""

        try:
            response = ""
            for chunk in self.agent.generate_content(prompt):
                if isinstance(chunk, str):
                    response += chunk

            response = response.strip()
            if response.startswith("```"):
                response = response.split("```")[1]
                if response.startswith("json"):
                    response = response[4:]
                response = response.strip()

            if not response:
                logger.warning("AI extraction returned empty response")
                return []

            data = json.loads(response)
            identifiers = data.get("tickers", [])

            # Resolve each identifier to ticker
            resolved_tickers = []
            for identifier in identifiers:
                ticker = self._resolve_identifier(identifier, allow_unresolved=True)
                if ticker:
                    resolved_tickers.append(ticker)

            return resolved_tickers[:4] if len(resolved_tickers) >= 2 else []

        except Exception as e:
            logger.error(f"AI extraction failed: {e}")
            return []

    def _resolve_identifier(self, identifier: str, allow_unresolved: bool = False) -> Optional[str]:
        """
        Resolve an identifier (ticker or company name) to a valid ticker.

        When allow_unresolved=True, returns the identifier as-is if it matches
        ticker format but isn't in our DB (for Google Search fallback).
        """
        identifier_upper = identifier.strip().upper()
        identifier_lower = identifier.strip().lower()

        # Try as direct ticker lookup
        company = self.connector.get_by_ticker(identifier_upper)
        if company:
            return identifier_upper

        # Try name matching
        all_companies = self.connector.get_all()

        # Stage 1: Exact substring match
        for company in all_companies:
            if not company.name:
                continue
            name_lower = company.name.lower()

            if identifier_lower in name_lower or name_lower in identifier_lower:
                logger.info(f"Matched '{identifier}' to '{company.name}' ({company.ticker})")
                return company.ticker

        # Stage 2: Token-based fuzzy matching
        common_words = {"inc", "corp", "ltd", "company", "the", "a", "an", "group", "holdings"}
        identifier_tokens = set(
            token for token in identifier_lower.split() if token not in common_words and len(token) > 2
        )

        if not identifier_tokens:
            return None

        best_match = None
        best_score = 0

        for company in all_companies:
            if not company.name:
                continue

            company_tokens = set(
                token for token in company.name.lower().split() if token not in common_words and len(token) > 2
            )

            matching_tokens = identifier_tokens & company_tokens
            if len(matching_tokens) > 0:
                score = len(matching_tokens) / len(identifier_tokens)
                if score > best_score and score >= 0.5:
                    best_score = score
                    best_match = company

        if best_match:
            logger.info(
                f"Fuzzy matched '{identifier}' to '{best_match.name}' ({best_match.ticker}) "
                f"with score {best_score:.2f}"
            )
            return best_match.ticker

        if allow_unresolved and len(identifier_upper) >= 2 and identifier_upper not in TICKER_STOPWORDS:
            logger.info(f"Allowing unresolved identifier as google_search target: {identifier_upper}")
            return identifier_upper

        logger.warning(f"Could not resolve identifier: {identifier}")
        return None
