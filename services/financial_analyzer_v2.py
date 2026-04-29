"""Financial analyzer v2 service (phase-5: company-general wiring only)."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

from ai_models.model_name import ModelName
from services.question_analyzer.classifier import QuestionClassifier
from services.question_analyzer.handlers_v2 import CompanyGeneralHandlerV2
from services.question_analyzer.types import QuestionType
from services.search_decision_engine import SearchDecisionEngine


class FinancialAnalyzerV2:
    """Main v2 analyzer; currently wires company-general path."""

    def __init__(
        self,
        classifier: Optional[QuestionClassifier] = None,
        search_decision_engine: Optional[SearchDecisionEngine] = None,
        company_general_handler: Optional[CompanyGeneralHandlerV2] = None,
    ):
        self.classifier = classifier or QuestionClassifier()
        self.search_decision_engine = search_decision_engine or SearchDecisionEngine()
        self.company_general_handler = company_general_handler or CompanyGeneralHandlerV2()

    async def analyze_question(
        self,
        ticker: str,
        question: str,
        use_url_context: bool = False,
        deep_analysis: bool = False,
        preferred_model: ModelName = ModelName.Auto,
        conversation_messages: Optional[List[Dict[str, str]]] = None,
        conversation_id: Optional[str] = None,
        anon_user_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        _ = (deep_analysis, conversation_id, anon_user_id)
        normalized_ticker = ticker.strip().upper() if ticker else ""
        if normalized_ticker in ["UNDEFINED", "NULL", ""]:
            normalized_ticker = "none"

        decision_coro = self.search_decision_engine.decide(
            question=question,
            ticker=normalized_ticker,
            is_etf=False,
        )
        classify_coro = self.classifier.classify_question_type(
            question, normalized_ticker, conversation_messages=conversation_messages
        )
        decision, classify_result = await asyncio.gather(decision_coro, classify_coro)
        classification, _comparison_tickers = classify_result

        yield {
            "type": "search_decision_meta",
            "body": {
                "search_decision": "on" if decision.use_google_search else "off",
                "reason_code": decision.reason_code,
                "decision_model": decision.decision_model,
                "decision_fallback": decision.decision_fallback,
                "confidence": decision.confidence,
            },
        }

        if not classification:
            yield {"type": "answer", "body": "❌ Unable to classify question type"}
            return

        if classification != QuestionType.COMPANY_GENERAL.value:
            yield {"type": "answer", "body": "❌ Unsupported question type in v2 phase-5"}
            return

        request_id = str(uuid.uuid4())
        async for chunk in self.company_general_handler.handle(
            ticker=ticker,
            question=question,
            search_decision=decision,
            use_url_context=use_url_context,
            preferred_model=preferred_model,
            conversation_messages=conversation_messages,
            request_id=request_id,
        ):
            yield chunk
