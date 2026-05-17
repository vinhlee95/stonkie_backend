from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Callable, Optional

from langfuse import observe

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from services.analyze_retrieval.retrieval import build_company_aware_query
from services.question_analyzer.context_builders.components import PromptComponents

logger = logging.getLogger(__name__)

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)

_MAX_QUERIES = 3

_REFORMULATION_PROMPT = """\
{current_date}

You are a search query optimizer for a financial research assistant. Your job is to convert a user's conversational question into max {max_queries} search-engine-optimized queries that will return the most relevant results from Brave Search.

Company: {company_name}
Ticker: {ticker}
User question: {question}

Rules:
- Output 1 query for simple/direct questions, up to {max_queries} for complex/multi-faceted questions
- Convert conversational language to search-engine keywords
- Include the company name (not ticker) in each query
- Add temporal context: current year/quarter when relevant
- Do NOT include specific research firm or data provider names (e.g. IDC, Canalys, Statista) — their pages are often paywalled. Instead use generic terms that surface articles citing those firms' data
  - Market share / shipments → "market share data", "shipment figures"
  - Financial filings → SEC, 10-K, 10-Q
  - Analyst estimates → consensus, forecast
- Disambiguate vague terms:
  - "share" → "market share" or "revenue share" depending on context
  - "breakdown" → "breakdown by segment" or "breakdown by region"
- Remove filler words, restructure for search relevance
- Each query should be concise (under 15 words)

Output ONLY JSON (no markdown) with exact keys:
- queries: list of 1-{max_queries} search-optimized query strings
- reasoning: one sentence explaining your reformulation choices

Examples:
- Question: "breakdown the company Mac share by region" (Apple) -> {{"queries": ["Apple Mac market share by region 2026 shipment data", "Apple Mac revenue breakdown geographic segment 2026"], "reasoning": "Disambiguated 'share' to market share and revenue, added temporal context"}}
- Question: "what's the latest on their AI strategy" (Microsoft) -> {{"queries": ["Microsoft AI strategy 2026 Copilot Azure OpenAI"], "reasoning": "Added specific AI product names and current year"}}
- Question: "how is revenue doing" (Tesla) -> {{"queries": ["Tesla revenue trend Q1 Q2 2026 quarterly results"], "reasoning": "Added temporal context and specificity"}}
"""


@dataclass
class ReformulationResult:
    queries: list[str]
    reasoning: str
    used_fallback: bool


class QueryReformulator:
    def __init__(
        self,
        model_name: ModelName = ModelName.Gemini25FlashNitro,
        classifier: Optional[Callable[[str, str, str], str]] = None,
    ):
        self.model_name = model_name
        self._classifier = classifier

    @observe(name="query_reformulator.reformulate")
    def reformulate(
        self,
        question: str,
        *,
        ticker: str,
        company_name: str,
    ) -> ReformulationResult:
        fallback_query = build_company_aware_query(question, ticker=ticker, company_name=company_name)
        try:
            raw = self._classify(question, ticker, company_name)
            parsed = self._parse_queries(raw)
            logger.info("QueryReformulator: %s → %s (%s)", question, parsed["queries"], parsed["reasoning"])
            return ReformulationResult(
                queries=parsed["queries"],
                reasoning=parsed["reasoning"],
                used_fallback=False,
            )
        except Exception as e:
            logger.warning("QueryReformulator failed (fallback to naive query): %s", e)
            return ReformulationResult(
                queries=[fallback_query],
                reasoning="",
                used_fallback=True,
            )

    def _classify(self, question: str, ticker: str, company_name: str) -> str:
        if self._classifier:
            return self._classifier(question, ticker, company_name)

        prompt = _REFORMULATION_PROMPT.format(
            current_date=PromptComponents.current_date(),
            company_name=company_name,
            ticker=ticker,
            question=question,
            max_queries=_MAX_QUERIES,
        )
        agent = MultiAgent(model_name=self.model_name)
        chunks = agent.generate_content(prompt=prompt, use_google_search=False)
        text = "".join(chunk for chunk in chunks if isinstance(chunk, str))
        return text.strip()

    @staticmethod
    def _parse_queries(raw: str) -> dict:
        match = _JSON_BLOCK_RE.search(raw)
        if not match:
            raise ValueError("No JSON object in classifier output")
        obj = json.loads(match.group(0))

        queries = obj.get("queries")
        if not isinstance(queries, list) or not queries:
            raise ValueError("'queries' must be a non-empty list")

        for item in queries:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("Each query must be a non-empty string")

        return {
            "queries": [q.strip() for q in queries[:_MAX_QUERIES]],
            "reasoning": obj.get("reasoning", ""),
        }
