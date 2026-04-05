"""LLM-based Google Search decision engine."""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Callable, Optional

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from ai_models.openrouter_client import get_openrouter_model_name

logger = logging.getLogger(__name__)

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)

# Patterns for historical/static company facts that never need search
_STATIC_FACT_PATTERNS = [
    re.compile(r"\b(who|whom)\b.*(found|establish|start|creat|incorporat)", re.IGNORECASE),
    re.compile(r"\bwhen\b.*(found|establish|start|creat|incorporat)", re.IGNORECASE),
    re.compile(r"\b(where)\b.*(headquarter|based|located|hq)", re.IGNORECASE),
    re.compile(r"\b(what|where|when)\b.*(ipo date|went public|go public|listed)", re.IGNORECASE),
    re.compile(r"\bnamed after\b|\bname (come|origin|meaning|etymol)", re.IGNORECASE),
    re.compile(r"\boriginal(ly)? (name|called)\b", re.IGNORECASE),
]


@dataclass(frozen=True)
class SearchDecision:
    use_google_search: bool
    reason_code: str
    confidence: float
    decision_model: str
    decision_fallback: str  # "classifier_fail_safe_on" | "none"


def _log_search_decision_result(decision: SearchDecision, ticker: str, is_etf: bool) -> None:
    """Emit one line with the resolved on/off search decision (mirrors SSE search_decision_meta)."""
    logger.info(
        "SearchDecisionEngine result: search=%s reason_code=%s confidence=%.2f decision_model=%s fallback=%s ticker=%s is_etf=%s",
        "on" if decision.use_google_search else "off",
        decision.reason_code,
        decision.confidence,
        decision.decision_model,
        decision.decision_fallback,
        ticker,
        is_etf,
    )


class SearchDecisionEngine:
    """Decides whether live Google Search should be enabled for a question."""

    def __init__(
        self,
        model_name: ModelName = ModelName.Gemini25FlashNitro,
        timeout_seconds: float = 5.0,
        classifier: Optional[Callable[[str, str, bool], str]] = None,
    ):
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self._classifier = classifier

    async def decide(
        self,
        question: str,
        ticker: str,
        is_etf: bool,
        force_google_search_reason: str | None = None,
        available_periods: dict[str, list] | None = None,
        available_metrics: list[str] | None = None,
    ) -> SearchDecision:
        if force_google_search_reason:
            decision = SearchDecision(
                use_google_search=True,
                reason_code=force_google_search_reason,
                confidence=1.0,
                decision_model=self.model_name.value,
                decision_fallback="none",
            )
            _log_search_decision_result(decision, ticker, is_etf)
            return decision

        # Fast path: skip search for obvious historical/static facts
        if self._is_static_fact(question):
            logger.info("SearchDecisionEngine fast path (keyword): static-fact pattern matched: %s", question[:80])
            decision = SearchDecision(
                use_google_search=False,
                reason_code="stable_concept",
                confidence=0.95,
                decision_model="keyword_prefilter",
                decision_fallback="none",
            )
            _log_search_decision_result(decision, ticker, is_etf)
            return decision

        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(self._classify_sync, question, ticker, is_etf, available_periods, available_metrics),
                timeout=self.timeout_seconds,
            )
            parsed = self._parse_decision(raw)
            decision = SearchDecision(
                use_google_search=parsed["use_google_search"],
                reason_code=parsed["reason_code"],
                confidence=parsed["confidence"],
                decision_model=self.model_name.value,
                decision_fallback="none",
            )
            _log_search_decision_result(decision, ticker, is_etf)
            return decision
        except Exception as e:
            logger.warning("SearchDecisionEngine classifier failed (fail-safe search ON): %s", e)
            decision = SearchDecision(
                use_google_search=True,
                reason_code="classifier_error",
                confidence=0.0,
                decision_model=self.model_name.value,
                decision_fallback="classifier_fail_safe_on",
            )
            _log_search_decision_result(decision, ticker, is_etf)
            return decision

    @staticmethod
    def _is_static_fact(question: str) -> bool:
        """Check if question matches known historical/static fact patterns."""
        return any(p.search(question) for p in _STATIC_FACT_PATTERNS)

    def _classify_sync(
        self,
        question: str,
        ticker: str,
        is_etf: bool,
        available_periods: dict[str, list] | None = None,
        available_metrics: list[str] | None = None,
    ) -> str:
        if self._classifier:
            logger.info("SearchDecisionEngine: using injected classifier (no LLM)")
            return self._classifier(question, ticker, is_etf)

        openrouter_id = get_openrouter_model_name(self.model_name)
        logger.info(
            "SearchDecisionEngine: classify LLM model=%s openrouter=%s",
            self.model_name.value,
            openrouter_id,
        )

        db_context = ""
        if available_periods and (available_periods.get("annual") or available_periods.get("quarterly")):
            annual = available_periods.get("annual", [])
            quarterly = available_periods.get("quarterly", [])
            metrics_line = ""
            metrics_instruction = "\nIMPORTANT: If the question asks about financial metrics (revenue, profit, earnings, etc.) for periods covered by the database, return false — database data is sufficient. Only return true when the question needs information the database CANNOT provide (e.g., news, real-time price, regulatory changes, events, analyst opinions)."
            if available_metrics:
                metrics_line = f"\n- Available metrics: {', '.join(available_metrics)}"
                metrics_instruction = "\nIMPORTANT: The database ONLY contains the metrics listed above. If the question asks about a metric NOT in the available metrics list (e.g., dividend per share, buyback amount, insider ownership), return true — the database does NOT have that data and web search is needed.\nIf the question asks about metrics that ARE in the list for periods covered by the database, return false — database data is sufficient."
            db_context = f"""
Financial data already available in our database for {ticker}:
- Annual statements: {annual if annual else "none"}
- Quarterly statements: {quarterly[:8] if quarterly else "none"}{metrics_line}
{metrics_instruction}
"""

        prompt = f"""
You are a strict JSON classifier deciding if live web search is needed for a finance assistant answer.

Question: {question}
Ticker context: {ticker or "none"}
Is ETF flow: {str(is_etf).lower()}
{db_context}
Rules:
- Return false for well-known historical facts about companies: founding date, founders, company origin, headquarters location, name etymology, IPO date, historical milestones — these don't change and are reliably in training data.
- Return false for stable educational concepts and timeless explanations.
- Return false when the database already has financial data covering the requested periods.
- Return true for time-sensitive asks: latest/current/today/now/news/recent/events/regulatory changes/price-now/real-time/current CEO/current leadership.
- If unsure, prefer true.
- Output ONLY JSON (no markdown) with exact keys:
  - use_google_search (boolean)
  - reason_code (string snake_case)
  - confidence (float between 0 and 1)

Examples:
- "Who founded Nike?" -> {{"use_google_search": false, "reason_code": "stable_concept", "confidence": 0.95}}
- "When was Apple established?" -> {{"use_google_search": false, "reason_code": "stable_concept", "confidence": 0.95}}
- "What is Tesla's IPO date?" -> {{"use_google_search": false, "reason_code": "stable_concept", "confidence": 0.9}}
- "Where is Microsoft headquartered?" -> {{"use_google_search": false, "reason_code": "stable_concept", "confidence": 0.95}}
- "Who is the current CEO of Nike?" -> {{"use_google_search": true, "reason_code": "time_sensitive", "confidence": 0.85}}
- "What is Nike's latest earnings?" -> {{"use_google_search": true, "reason_code": "latest_info", "confidence": 0.95}}
- "What is FORTUM.HE's dividend per share?" (DB has revenue, net income but NOT dividend per share) -> {{"use_google_search": true, "reason_code": "db_metric_missing", "confidence": 0.9}}

Allowed reason_code values:
time_sensitive, latest_info, stable_concept, db_data_sufficient, db_metric_missing, ambiguous_default_on, other
"""
        agent = MultiAgent(model_name=self.model_name)
        chunks = agent.generate_content(prompt=prompt, use_google_search=False)
        text = "".join(chunk for chunk in chunks if isinstance(chunk, str))
        return text.strip()

    @staticmethod
    def _parse_decision(raw: str) -> dict:
        match = _JSON_BLOCK_RE.search(raw)
        if not match:
            raise ValueError("No JSON object in classifier output")
        obj = json.loads(match.group(0))

        if not isinstance(obj.get("use_google_search"), bool):
            raise ValueError("Invalid use_google_search")
        reason_code = obj.get("reason_code")
        if not isinstance(reason_code, str) or not reason_code.strip():
            raise ValueError("Invalid reason_code")

        confidence = obj.get("confidence", 0.0)
        if isinstance(confidence, int):
            confidence = float(confidence)
        if not isinstance(confidence, float):
            raise ValueError("Invalid confidence type")
        confidence = max(0.0, min(1.0, confidence))

        return {
            "use_google_search": obj["use_google_search"],
            "reason_code": reason_code.strip(),
            "confidence": confidence,
        }
