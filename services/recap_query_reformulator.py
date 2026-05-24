from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional

from langfuse import observe

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from services.question_analyzer.context_builders.components import PromptComponents

logger = logging.getLogger(__name__)

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)

_MAX_QUERIES = 3

_REFORMULATION_PROMPT = """\
{current_date}

You are a search query optimizer for a financial research assistant. Your job is to convert a user's conversational question into max {max_queries} search-engine-optimized queries that will return the most relevant results from Brave Search.

Market: {market}
Recap period: {period_start} to {period_end}
User question: {question}

Rules:
- Output 1 query for simple/direct questions, up to {max_queries} for complex/multi-faceted questions
- Convert conversational language to search-engine keywords
- Add temporal context: current year/quarter when relevant
- Do NOT include specific research firm or data provider names (e.g. IDC, Canalys, Statista) — their pages are often paywalled. Instead use generic terms that surface articles citing those firms' data
- Disambiguate vague terms:
  - "share" → "market share" or "revenue share" depending on context
  - "index" → specify the actual index name if inferable (e.g. "Buffett Indicator", "S&P 500", "VIX")
- Remove filler words, restructure for search relevance
- Each query should be concise (under 15 words)

Output ONLY JSON (no markdown) with exact keys:
- queries: list of 1-{max_queries} search-optimized query strings
- reasoning: one sentence explaining your reformulation choices

Examples:
- Question: "what's the current buffet index?" (US, 2026-05-19 to 2026-05-22) -> {{"queries": ["Buffett Indicator current value total market cap GDP ratio 2026"], "reasoning": "Corrected spelling, expanded to full indicator name with components"}}
- Question: "how are emerging markets doing?" (GLOBAL, 2026-05-19 to 2026-05-22) -> {{"queries": ["emerging markets performance May 2026", "MSCI emerging markets index 2026"], "reasoning": "Added temporal context and benchmark index"}}
- Question: "what's the yield curve saying?" (US, 2026-05-19 to 2026-05-22) -> {{"queries": ["US Treasury yield curve inversion 2026 recession signal"], "reasoning": "Added specific terms for yield curve analysis context"}}
"""


@dataclass
class ReformulationResult:
    queries: list[str]
    reasoning: str
    used_fallback: bool


class RecapQueryReformulator:
    """Query reformulator for recap analyze. Binds market/period at init so
    ``reformulate`` matches the signature expected by ``retrieve_for_analyze``
    (question, *, ticker, company_name) — ticker/company_name are ignored."""

    def __init__(
        self,
        *,
        market: str,
        period_start: date,
        period_end: date,
        model_name: ModelName = ModelName.Gemini25FlashNitro,
        classifier: Optional[Callable[[str, str, date, date], str]] = None,
    ):
        self.model_name = model_name
        self._market = market
        self._period_start = period_start
        self._period_end = period_end
        self._classifier = classifier

    @observe(name="recap_query_reformulator.reformulate")
    def reformulate(
        self,
        question: str,
        *,
        ticker: str = "",
        company_name: str = "",
    ) -> ReformulationResult:
        fallback_query = f"{question} {self._market}"
        try:
            raw = self._classify(question)
            parsed = self._parse_queries(raw)
            logger.info("RecapQueryReformulator: %s → %s (%s)", question, parsed["queries"], parsed["reasoning"])
            return ReformulationResult(
                queries=parsed["queries"],
                reasoning=parsed["reasoning"],
                used_fallback=False,
            )
        except Exception as e:
            logger.warning("RecapQueryReformulator failed (fallback to raw query): %s", e)
            return ReformulationResult(
                queries=[fallback_query],
                reasoning="",
                used_fallback=True,
            )

    def _classify(self, question: str) -> str:
        if self._classifier:
            return self._classifier(question, self._market, self._period_start, self._period_end)

        prompt = _REFORMULATION_PROMPT.format(
            current_date=PromptComponents.current_date(),
            market=self._market,
            period_start=self._period_start.isoformat(),
            period_end=self._period_end.isoformat(),
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
