"""Single-call question router for the v2 analyze flow.

One structured-JSON LLM call replaces classify_question_type + SearchDecisionEngine +
classify_data_and_period_requirement + QueryReformulator. It also sees the recent conversation, so
follow-ups are rewritten into a standalone question used for retrieval. On parse failure or timeout
`QuestionRouter.route` returns None and the caller falls back to the old calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, replace
from typing import Callable, Dict, List, Optional

from langfuse import observe

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from core.financial_statement_type import FinancialStatementType
from services.etf_question_analyzer.types import ETFDataRequirement, ETFQuestionType
from services.search_decision_engine import SearchDecision, is_static_fact

from .classifier import QuestionClassifier
from .context_builders.components import PromptComponents
from .types import FinancialDataRequirement, FinancialPeriodRequirement, QuestionType

logger = logging.getLogger(__name__)

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
_TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")

MAX_SEARCH_QUERIES = 3
_MAX_STANDALONE_CHARS = 500
# Search-query caps shared by the router and every query built outside it (Brave rejects >400 chars / 50 words).
MAX_QUERY_CHARS = 200
MAX_QUERY_WORDS = 40
_CONVERSATION_MESSAGES = 4  # last 2 Q/A turns
_USER_MESSAGE_CHARS = 500
_ASSISTANT_MESSAGE_CHARS = 300

ROUTER_MODEL_TAG = "router"

_REASON_CODES = {
    "time_sensitive",
    "latest_info",
    "stable_concept",
    "db_data_sufficient",
    "db_metric_missing",
    "ambiguous_default_on",
    "other",
}
_QUESTION_TYPES = {qt.value for qt in QuestionType}
_DATA_REQUIREMENTS = {
    FinancialDataRequirement.NONE,
    FinancialDataRequirement.BASIC,
    FinancialDataRequirement.DETAILED,
    FinancialDataRequirement.QUARTERLY_SUMMARY,
    FinancialDataRequirement.ANNUAL_SUMMARY,
}
_PERIOD_TYPES = {"annual", "quarterly", "both"}
# Comparisons are detected by ETFTickerExtractor (2+ resolved tickers), never by the router.
_ETF_QUESTION_TYPES = {
    ETFQuestionType.GENERAL_ETF,
    ETFQuestionType.ETF_OVERVIEW,
    ETFQuestionType.ETF_DETAILED_ANALYSIS,
}
# A fund-specific question always needs the fund's row; the handler's builder is fixed by the type.
_ETF_MIN_REQUIREMENT = {
    ETFQuestionType.ETF_OVERVIEW: ETFDataRequirement.BASIC,
    ETFQuestionType.ETF_DETAILED_ANALYSIS: ETFDataRequirement.DETAILED,
}


@dataclass(frozen=True)
class RoutingResult:
    question_type: str  # QuestionType value
    comparison_tickers: list[str]
    data_requirement: FinancialDataRequirement
    period_requirement: Optional[FinancialPeriodRequirement]
    relevant_statements: Optional[list[FinancialStatementType]]
    search_decision: SearchDecision
    search_queries: list[str]
    standalone_question: str


@dataclass(frozen=True)
class ETFRoutingResult:
    question_type: ETFQuestionType  # never ETF_COMPARISON (the ticker extractor decides comparisons)
    data_requirement: ETFDataRequirement
    search_decision: SearchDecision
    search_queries: list[str]
    standalone_question: str


def _has_valid_ticker(ticker: str | None) -> bool:
    return bool(ticker and ticker.strip() and ticker.strip().upper() not in {"UNDEFINED", "NULL", "NONE"})


def _format_conversation(conversation_messages: Optional[List[Dict[str, str]]]) -> str:
    if not conversation_messages:
        return ""
    lines = []
    for msg in conversation_messages[-_CONVERSATION_MESSAGES:]:
        role = (msg.get("role") or "").strip().lower()
        content = " ".join((msg.get("content") or "").split())
        if not content:
            continue
        limit = _USER_MESSAGE_CHARS if role == "user" else _ASSISTANT_MESSAGE_CHARS
        if len(content) > limit:
            content = content[:limit] + "..."
        lines.append(f"{role.upper() or 'USER'}: {content}")
    if not lines:
        return ""
    return "Recent conversation (oldest first):\n" + "\n".join(lines) + "\n"


def _format_db_context(
    ticker: str, available_periods: dict[str, list] | None, available_metrics: list[str] | None
) -> str:
    if not available_periods or not (available_periods.get("annual") or available_periods.get("quarterly")):
        return f"Our database has no financial statements for {ticker}.\n" if _has_valid_ticker(ticker) else ""
    annual = available_periods.get("annual") or []
    quarterly = available_periods.get("quarterly") or []
    lines = [
        f"Financial data in our database for {ticker}:",
        f"- Annual statements: {annual if annual else 'none'}",
        f"- Quarterly statements: {quarterly[:8] if quarterly else 'none'}",
    ]
    if available_metrics:
        lines.append(f"- Available metrics (ONLY these): {', '.join(available_metrics)}")
        lines.append(
            "- The database has NO segment/geographic/product-line breakdowns, dividends, buybacks, analyst "
            "estimates, news or prices."
        )
    return "\n".join(lines) + "\n"


def build_router_prompt(
    *,
    question: str,
    ticker: str,
    conversation_messages: Optional[List[Dict[str, str]]] = None,
    available_periods: dict[str, list] | None = None,
    available_metrics: list[str] | None = None,
) -> str:
    has_ticker = _has_valid_ticker(ticker)
    ticker_line = ticker.strip().upper() if has_ticker else "none (no company selected)"
    conversation = _format_conversation(conversation_messages)
    db_context = _format_db_context(ticker.strip().upper() if has_ticker else "", available_periods, available_metrics)

    return f"""{PromptComponents.current_date()}

You route questions for a stock-research chat assistant. Decide how to answer the LATEST question and return ONE JSON object.
The question may be in any language; decide by meaning.

Ticker the user is viewing: {ticker_line}
{db_context}{conversation}
LATEST question: {question}

Fields:

1. standalone_question (string): the latest question rewritten so it is fully understandable on its own. Resolve "it", "that", "they", "the company", "this stock" and topics implied by the recent conversation (e.g. after a question about Nvidia's data center revenue, "How does that compare with AMD?" -> "How does Nvidia's data center revenue last quarter compare with AMD's?"). Keep the user's language. If it is already standalone, copy it unchanged.

2. question_type, exactly one of:
   - "company-specific-finance": needs the viewed company's financial statements, metrics or performance (revenue, profit, margins, earnings, cash flow, debt, growth, quarterly/annual results, valuation, dividends, buy/sell). ONLY when a ticker is given.
   - "company-general": about the viewed company but not financial analysis (mission, CEO, products, history, headquarters, news).
   - "general-finance": general financial concepts, markets, strategy, individuals, or any question when no ticker is given and no company is compared.
   - "company-comparison": compares 2+ specific companies.
   If the latest question is vague (e.g. "Tell me more", "What about that?"), classify it by the conversation topic.

3. comparison_tickers (array of strings): for company-comparison, the 2-4 official tickers compared (include the viewed ticker when "this"/"it" refers to it); otherwise [].

4. data_requirement (company-specific-finance only, otherwise "none"), exactly one of:
   - "none": no financial data needed, or needs data the database lacks (segment/geographic/product breakdowns)
   - "basic": market cap, P/E, basic ratios, "is it profitable?"
   - "detailed": statement data (revenue, expenses, margins, cash flow, debt) available in the metrics above; also buy/sell, valuation and outlook questions ("Should I buy X?", "Is X overvalued?"), which need the full financial picture (period_type "both", num_periods 3, all three statements)
   - "quarterly_summary": an overall summary/highlights of the latest quarterly results or earnings report ("How did the latest quarter go?", "Summarize the 10-Q")
   - "annual_summary": an overall summary/highlights of the latest annual results or 10-K
   A question about specific metrics (net income, revenue, debt, margins) for any period is "detailed", never a summary.

5. period_requirement: null unless data_requirement is detailed, quarterly_summary or annual_summary. Otherwise an object:
   {{"period_type": "annual"|"quarterly"|"both", "specific_years": [2023]|null, "specific_quarters": ["2024-Q3"]|["latest"]|null, "num_periods": 3|null}}
   - Fill only one of specific_years, specific_quarters, num_periods; except ["latest"] which also sets num_periods: 1.
   - No specific period mentioned: num_periods 3-5. Default to annual unless quarterly is mentioned.
   - "latest", "recent", "current", "this quarter", "last quarter" without a year -> {{"period_type": "quarterly", "specific_years": null, "specific_quarters": ["latest"], "num_periods": 1}}
   - Quarters use "YYYY-Q#".

6. relevant_statements: for detailed, an array from "income_statement", "balance_sheet", "cash_flow" (all three for broad questions); otherwise null.

7. use_web_search (boolean) and reason_code, one of: time_sensitive, latest_info, stable_concept, db_data_sufficient, db_metric_missing, ambiguous_default_on, other.
   - false + stable_concept: well-known historical facts (founding, founders, headquarters, IPO date, name origin) and timeless educational concepts.
   - false + db_data_sufficient: the question asks for metrics that ARE in the database metrics for periods the database covers.
   - true + db_metric_missing: asks for a metric NOT in the database metrics (e.g. dividend per share, buybacks).
   - true + time_sensitive / latest_info: latest/current/today/now/news/recent events, price now, analyst opinions, current CEO, buy/sell decisions.
   - true + latest_info: the latest/most recent quarter or year, earnings, or a summary of a quarterly/annual report (the newest results may be newer than our database).
   - If unsure, true + ambiguous_default_on.

8. search_queries (array of strings): when use_web_search is true, 1-{MAX_SEARCH_QUERIES} web-search queries built from standalone_question (1 for simple questions, up to {MAX_SEARCH_QUERIES} for multi-part ones). Search-engine keywords, under 15 words each, include the company NAME (not ticker), add the current year/quarter when relevant, no research-firm names (IDC, Canalys, Statista). When use_web_search is false: [].

Output ONLY the JSON object, no markdown, with exactly these keys:
{{"standalone_question": "...", "question_type": "...", "comparison_tickers": [], "data_requirement": "...", "period_requirement": null, "relevant_statements": null, "use_web_search": true, "reason_code": "...", "search_queries": ["..."]}}"""


def _extract_json_object(raw: str) -> dict:
    match = _JSON_BLOCK_RE.search(raw or "")
    if not match:
        raise ValueError("No JSON object in router output")
    obj = json.loads(match.group(0))
    if not isinstance(obj, dict):
        raise ValueError("Router output is not a JSON object")
    return obj


def _fallback_period(data_requirement: FinancialDataRequirement) -> FinancialPeriodRequirement:
    if data_requirement == FinancialDataRequirement.QUARTERLY_SUMMARY:
        return FinancialPeriodRequirement(period_type="quarterly", num_periods=1)
    if data_requirement == FinancialDataRequirement.ANNUAL_SUMMARY:
        return FinancialPeriodRequirement(period_type="annual", num_periods=1)
    return FinancialPeriodRequirement(period_type="annual", num_periods=3)


def _parse_period(block: object, data_requirement: FinancialDataRequirement) -> Optional[FinancialPeriodRequirement]:
    if data_requirement not in (
        FinancialDataRequirement.DETAILED,
        FinancialDataRequirement.QUARTERLY_SUMMARY,
        FinancialDataRequirement.ANNUAL_SUMMARY,
    ):
        return None
    if not isinstance(block, dict):
        return _fallback_period(data_requirement)

    period_type = block.get("period_type")
    if period_type not in _PERIOD_TYPES:
        raise ValueError(f"Invalid period_type: {period_type!r}")
    specific_years = block.get("specific_years")
    if specific_years is not None and (
        not isinstance(specific_years, list)
        or not all(isinstance(y, int) and not isinstance(y, bool) for y in specific_years)
    ):
        raise ValueError(f"Invalid specific_years: {specific_years!r}")
    specific_quarters = block.get("specific_quarters")
    if specific_quarters is not None and (
        not isinstance(specific_quarters, list) or not all(isinstance(q, str) for q in specific_quarters)
    ):
        raise ValueError(f"Invalid specific_quarters: {specific_quarters!r}")
    num_periods = block.get("num_periods")
    if num_periods is not None and (not isinstance(num_periods, int) or isinstance(num_periods, bool)):
        raise ValueError(f"Invalid num_periods: {num_periods!r}")
    return FinancialPeriodRequirement(
        period_type=period_type,
        specific_years=specific_years or None,
        specific_quarters=specific_quarters or None,
        num_periods=num_periods,
    )


def _parse_statements(
    raw: object, data_requirement: FinancialDataRequirement
) -> Optional[list[FinancialStatementType]]:
    """Same contract as QuestionClassifier: DETAILED always gets a non-empty ordered list, else None."""
    if data_requirement != FinancialDataRequirement.DETAILED:
        return None
    deduped: list[FinancialStatementType] = []
    for item in raw if isinstance(raw, list) else []:
        try:
            statement = FinancialStatementType(item)
        except ValueError:
            continue
        if statement not in deduped:
            deduped.append(statement)
    if not deduped or set(deduped) == set(FinancialStatementType):
        return list(FinancialStatementType.all_ordered())
    return deduped


def trim_search_query(query: str) -> str:
    """Collapse whitespace and cap a web-search query at MAX_QUERY_WORDS words and MAX_QUERY_CHARS chars."""
    return " ".join(query.split()[:MAX_QUERY_WORDS])[:MAX_QUERY_CHARS].strip()


def _parse_queries(raw: object) -> list[str]:
    if not isinstance(raw, list):
        raise ValueError("search_queries must be a list")
    queries: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("search_queries items must be strings")
        query = trim_search_query(item)
        if query and query not in queries:
            queries.append(query)
    return queries[:MAX_SEARCH_QUERIES]


def _parse_comparison_tickers(raw: object) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("comparison_tickers must be a list")
    tickers: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError("comparison_tickers items must be strings")
        ticker = item.strip().upper()
        if _TICKER_RE.match(ticker) and ticker not in tickers:
            tickers.append(ticker)
    return tickers[:4]


def parse_routing_response(raw: str, *, question: str, ticker: str, model_name: str) -> RoutingResult:
    """Strictly validate the router's JSON. Raises ValueError on anything the caller can't trust."""
    obj = _extract_json_object(raw)

    question_type = obj.get("question_type")
    if question_type not in _QUESTION_TYPES:
        raise ValueError(f"Invalid question_type: {question_type!r}")

    raw_requirement = obj.get("data_requirement")
    try:
        data_requirement = FinancialDataRequirement(raw_requirement)
    except ValueError as e:
        raise ValueError(f"Invalid data_requirement: {raw_requirement!r}") from e
    if data_requirement not in _DATA_REQUIREMENTS:
        raise ValueError(f"Invalid data_requirement: {raw_requirement!r}")

    use_web_search = obj.get("use_web_search")
    if not isinstance(use_web_search, bool):
        raise ValueError(f"Invalid use_web_search: {use_web_search!r}")
    reason_code = obj.get("reason_code")
    if not isinstance(reason_code, str) or not reason_code.strip():
        raise ValueError(f"Invalid reason_code: {reason_code!r}")
    reason_code = reason_code.strip()
    if reason_code not in _REASON_CODES:
        reason_code = "other"

    search_queries = _parse_queries(obj.get("search_queries", []))
    if use_web_search and not search_queries:
        raise ValueError("use_web_search is true but search_queries is empty")

    standalone = obj.get("standalone_question")
    if not isinstance(standalone, str) or not standalone.strip():
        raise ValueError("Missing standalone_question")
    standalone = " ".join(standalone.split())[:_MAX_STANDALONE_CHARS]

    # Same guard as the old classifier prompt: no company-specific analysis without a ticker.
    if question_type == QuestionType.COMPANY_SPECIFIC_FINANCE.value and not _has_valid_ticker(ticker):
        question_type = QuestionType.GENERAL_FINANCE.value

    return RoutingResult(
        question_type=question_type,
        comparison_tickers=_parse_comparison_tickers(obj.get("comparison_tickers")),
        data_requirement=data_requirement,
        period_requirement=_parse_period(obj.get("period_requirement"), data_requirement),
        relevant_statements=_parse_statements(obj.get("relevant_statements"), data_requirement),
        search_decision=SearchDecision(
            use_google_search=use_web_search,
            reason_code=reason_code,
            confidence=0.9,
            decision_model=f"{ROUTER_MODEL_TAG}:{model_name}",
            decision_fallback="none",
        ),
        search_queries=search_queries if use_web_search else [],
        standalone_question=standalone,
    )


def apply_fast_paths(
    result: RoutingResult, *, question: str, force_google_search_reason: str | None = None
) -> RoutingResult:
    """Deterministic overrides kept from the old pipeline (keyword pre-filters + forced search)."""
    decision = result.search_decision
    if force_google_search_reason:
        decision = replace(decision, use_google_search=True, reason_code=force_google_search_reason, confidence=1.0)
    elif is_static_fact(question):
        decision = replace(
            decision,
            use_google_search=False,
            reason_code="stable_concept",
            confidence=0.95,
            decision_model="keyword_prefilter",
        )

    data_requirement = result.data_requirement
    period_requirement = result.period_requirement
    relevant_statements = result.relevant_statements
    question_lower = question.lower()
    if any(k in question_lower for k in QuestionClassifier.QUARTERLY_REPORT_KEYWORDS):
        data_requirement, period_requirement, relevant_statements = (
            FinancialDataRequirement.QUARTERLY_SUMMARY,
            None,
            None,
        )
    elif any(k in question_lower for k in QuestionClassifier.ANNUAL_REPORT_KEYWORDS):
        data_requirement, period_requirement, relevant_statements = FinancialDataRequirement.ANNUAL_SUMMARY, None, None

    search_queries = result.search_queries
    if decision.use_google_search and not search_queries:
        search_queries = [result.standalone_question]
    if not decision.use_google_search:
        search_queries = []

    return replace(
        result,
        search_decision=decision,
        data_requirement=data_requirement,
        period_requirement=period_requirement,
        relevant_statements=relevant_statements,
        search_queries=search_queries,
    )


def _format_etf_db_context(
    ticker: str, etf_name: str | None, fund_provider: str | None, index_tracked: str | None
) -> str:
    if not _has_valid_ticker(ticker):
        return ""
    if not etf_name:
        return f"Our database has no data for {ticker}.\n"
    lines = [f"ETF data in our database for {ticker}:", f"- Name: {etf_name}"]
    if fund_provider:
        lines.append(f"- Provider: {fund_provider}")
    if index_tracked:
        lines.append(f"- Index tracked: {index_tracked}")
    lines.append(
        "- The database also has TER, fund size, replication, distribution policy, domicile, launch date, "
        "holdings, sector and country allocation; it has NO performance, returns, prices, NAV, flows, "
        "dividend yield or news."
    )
    return "\n".join(lines) + "\n"


def build_etf_router_prompt(
    *,
    question: str,
    ticker: str,
    conversation_messages: Optional[List[Dict[str, str]]] = None,
    etf_name: str | None = None,
    fund_provider: str | None = None,
    index_tracked: str | None = None,
) -> str:
    has_ticker = _has_valid_ticker(ticker)
    normalized = ticker.strip().upper() if has_ticker else ""
    ticker_line = normalized if has_ticker else "none (no ETF selected)"
    conversation = _format_conversation(conversation_messages)
    db_context = _format_etf_db_context(normalized, etf_name, fund_provider, index_tracked)

    return f"""{PromptComponents.current_date()}

You route questions for an ETF-research chat assistant. Decide how to answer the LATEST question and return ONE JSON object.
The question may be in any language; decide by meaning.

ETF the user is viewing: {ticker_line}
{db_context}{conversation}
LATEST question: {question}

Fields:

1. standalone_question (string): the latest question rewritten so it is fully understandable on its own. Resolve "it", "this ETF", "this fund", "that" and topics implied by the recent conversation, naming the fund by its NAME (e.g. after a question about SXR8's fees, "What about its top holdings?" -> "What are the top holdings of the iShares Core S&P 500 UCITS ETF?"). Keep the user's language. If it is already standalone, copy it unchanged.

2. question_type, exactly one of:
   - "general_etf": general ETF education not specific to the viewed fund (what is an ETF, TER, tracking difference, physical vs synthetic replication, accumulating vs distributing), or any question when no ETF is selected.
   - "etf_overview": basic facts about the viewed ETF: TER/expense ratio, provider, index tracked, fund size, launch date, domicile, replication, distribution policy, currency.
   - "etf_detailed_analysis": the viewed ETF's holdings, sector or country allocation, concentration, diversification, performance, risk, suitability, or whether to buy it.
   If the latest question is vague (e.g. "Tell me more"), classify it by the conversation topic.

3. data_requirement, exactly one of: "none" (general_etf), "basic" (etf_overview: core metadata only), "detailed" (etf_detailed_analysis: holdings, sectors, countries).

4. use_web_search (boolean) and reason_code, one of: time_sensitive, latest_info, stable_concept, db_data_sufficient, db_metric_missing, ambiguous_default_on, other.
   - false + stable_concept: timeless ETF concepts and stable facts (what an ETF is, how replication works, launch date, domicile).
   - false + db_data_sufficient: the database fields listed above answer it (e.g. TER, provider, index tracked, holdings, sector or country weights).
   - true + db_metric_missing: needs data the database lacks (performance, returns, dividend yield, tracking difference).
   - true + time_sensitive / latest_info: latest/current/today/news, price, NAV, flows, recent changes, buy/sell decisions.
   - If unsure, true + ambiguous_default_on.

5. search_queries (array of strings): when use_web_search is true, 1-{MAX_SEARCH_QUERIES} web-search queries built from standalone_question (1 for simple questions, up to {MAX_SEARCH_QUERIES} for multi-part ones). Search-engine keywords, under 15 words each, use the fund NAME (not only the ticker), add the current year when relevant. When use_web_search is false: [].

Output ONLY the JSON object, no markdown, with exactly these keys:
{{"standalone_question": "...", "question_type": "...", "data_requirement": "...", "use_web_search": true, "reason_code": "...", "search_queries": ["..."]}}"""


def parse_etf_routing_response(raw: str, *, question: str, ticker: str, model_name: str) -> ETFRoutingResult:
    """Strictly validate the ETF router's JSON. Raises ValueError on anything the caller can't trust."""
    _ = question
    obj = _extract_json_object(raw)

    raw_type = obj.get("question_type")
    try:
        question_type = ETFQuestionType(raw_type)
    except ValueError as e:
        raise ValueError(f"Invalid question_type: {raw_type!r}") from e
    if question_type not in _ETF_QUESTION_TYPES:
        raise ValueError(f"Invalid question_type: {raw_type!r}")

    raw_requirement = obj.get("data_requirement")
    try:
        data_requirement = ETFDataRequirement(raw_requirement)
    except ValueError as e:
        raise ValueError(f"Invalid data_requirement: {raw_requirement!r}") from e

    use_web_search = obj.get("use_web_search")
    if not isinstance(use_web_search, bool):
        raise ValueError(f"Invalid use_web_search: {use_web_search!r}")
    reason_code = obj.get("reason_code")
    if not isinstance(reason_code, str) or not reason_code.strip():
        raise ValueError(f"Invalid reason_code: {reason_code!r}")
    reason_code = reason_code.strip()
    if reason_code not in _REASON_CODES:
        reason_code = "other"

    search_queries = _parse_queries(obj.get("search_queries", []))
    if use_web_search and not search_queries:
        raise ValueError("use_web_search is true but search_queries is empty")

    standalone = obj.get("standalone_question")
    if not isinstance(standalone, str) or not standalone.strip():
        raise ValueError("Missing standalone_question")
    standalone = " ".join(standalone.split())[:_MAX_STANDALONE_CHARS]

    # Same guard as the v1 ETF classifier: no fund-specific analysis without a ticker.
    if not _has_valid_ticker(ticker):
        question_type, data_requirement = ETFQuestionType.GENERAL_ETF, ETFDataRequirement.NONE
    elif data_requirement == ETFDataRequirement.NONE and question_type in _ETF_MIN_REQUIREMENT:
        data_requirement = _ETF_MIN_REQUIREMENT[question_type]

    return ETFRoutingResult(
        question_type=question_type,
        data_requirement=data_requirement,
        search_decision=SearchDecision(
            use_google_search=use_web_search,
            reason_code=reason_code,
            confidence=0.9,
            decision_model=f"{ROUTER_MODEL_TAG}:{model_name}",
            decision_fallback="none",
        ),
        search_queries=search_queries if use_web_search else [],
        standalone_question=standalone,
    )


def apply_etf_fast_paths(result: ETFRoutingResult, *, question: str) -> ETFRoutingResult:
    """Deterministic overrides kept from the old pipeline (static-fact keyword pre-filter)."""
    if not is_static_fact(question):
        return result
    decision = replace(
        result.search_decision,
        use_google_search=False,
        reason_code="stable_concept",
        confidence=0.95,
        decision_model="keyword_prefilter",
    )
    return replace(result, search_decision=decision, search_queries=[])


def default_etf_routing(*, question: str, ticker: str) -> ETFRoutingResult:
    """Routing used when the router fails: no LLM call, search on (queries are built by the handler)."""
    if _has_valid_ticker(ticker):
        question_type, data_requirement = ETFQuestionType.ETF_OVERVIEW, ETFDataRequirement.BASIC
    else:
        question_type, data_requirement = ETFQuestionType.GENERAL_ETF, ETFDataRequirement.NONE
    return ETFRoutingResult(
        question_type=question_type,
        data_requirement=data_requirement,
        search_decision=SearchDecision(
            use_google_search=True,
            reason_code="ambiguous_default_on",
            confidence=0.5,
            decision_model="etf_default_routing",
            decision_fallback="router_fail_safe_on",
        ),
        search_queries=[],
        standalone_question=" ".join(question.split())[:_MAX_STANDALONE_CHARS] or question,
    )


class QuestionRouter:
    """One LLM call that routes a v2 analyze question. Returns None when the caller should fall back."""

    def __init__(
        self,
        model_name: ModelName = ModelName.Gemini31FlashLite,
        timeout_seconds: float = 3.0,
        llm: Optional[Callable[[str], str]] = None,
    ):
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self._llm = llm

    def _generate(self, prompt: str) -> str:
        if self._llm:
            return self._llm(prompt)
        return MultiAgent(model_name=self.model_name).generate_text(prompt).strip()

    @observe(name="question_router.route")
    async def route(
        self,
        *,
        question: str,
        ticker: str,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
        available_periods: dict[str, list] | None = None,
        available_metrics: list[str] | None = None,
    ) -> Optional[RoutingResult]:
        t_start = time.perf_counter()
        prompt = build_router_prompt(
            question=question,
            ticker=ticker,
            conversation_messages=conversation_messages,
            available_periods=available_periods,
            available_metrics=available_metrics,
        )
        try:
            raw = await asyncio.wait_for(asyncio.to_thread(self._generate, prompt), timeout=self.timeout_seconds)
            result = parse_routing_response(raw, question=question, ticker=ticker, model_name=self.model_name.value)
        except Exception as e:
            logger.warning(
                "QuestionRouter failed after %.2fs (falling back to legacy routing): %s: %s",
                time.perf_counter() - t_start,
                type(e).__name__,
                e,
            )
            return None
        logger.info(
            "QuestionRouter %.2fs: type=%s data=%s search=%s reason=%s queries=%s standalone=%r",
            time.perf_counter() - t_start,
            result.question_type,
            result.data_requirement.value,
            result.search_decision.use_google_search,
            result.search_decision.reason_code,
            result.search_queries,
            result.standalone_question,
        )
        return result

    @observe(name="question_router.route_etf")
    async def route_etf(
        self,
        *,
        question: str,
        ticker: str,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
        etf_name: str | None = None,
        fund_provider: str | None = None,
        index_tracked: str | None = None,
    ) -> Optional[ETFRoutingResult]:
        """ETF mode of route(): same timeout and None-on-failure contract."""
        t_start = time.perf_counter()
        prompt = build_etf_router_prompt(
            question=question,
            ticker=ticker,
            conversation_messages=conversation_messages,
            etf_name=etf_name,
            fund_provider=fund_provider,
            index_tracked=index_tracked,
        )
        try:
            raw = await asyncio.wait_for(asyncio.to_thread(self._generate, prompt), timeout=self.timeout_seconds)
            result = parse_etf_routing_response(raw, question=question, ticker=ticker, model_name=self.model_name.value)
        except Exception as e:
            logger.warning(
                "QuestionRouter ETF failed after %.2fs (falling back to default ETF routing): %s: %s",
                time.perf_counter() - t_start,
                type(e).__name__,
                e,
            )
            return None
        logger.info(
            "QuestionRouter ETF %.2fs: type=%s data=%s search=%s reason=%s queries=%s standalone=%r",
            time.perf_counter() - t_start,
            result.question_type.value,
            result.data_requirement.value,
            result.search_decision.use_google_search,
            result.search_decision.reason_code,
            result.search_queries,
            result.standalone_question,
        )
        return result
