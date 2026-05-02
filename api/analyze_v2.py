"""HTTP route for stock analyze v2."""

from __future__ import annotations

import asyncio
import json
import os
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ai_models.model_mapper import map_frontend_model_to_enum
from services.analyze_v2_stream import AnalyzeV2StreamService
from services.financial_analyzer_v2 import FinancialAnalyzerV2
from services.search_decision_engine import SearchDecisionEngine

router = APIRouter()

search_decision_engine_v2 = SearchDecisionEngine()
financial_analyzer_v2 = FinancialAnalyzerV2(search_decision_engine=search_decision_engine_v2)
analyze_v2_stream_service = AnalyzeV2StreamService(financial_analyzer_v2)


@router.post("/api/v2/companies/{ticker}/analyze")
async def analyze_financial_data_v2(ticker: str, request: Request) -> StreamingResponse:
    try:
        body = await request.json()
        question = body.get("question")
        use_url_context = body.get("useUrlContext", False)
        deep_analysis = body.get("deepAnalysis", False)
        preferred_model_str = body.get("preferredModel", "fastest")
        conversation_id = body.get("conversationId")

        if not question:
            raise HTTPException(status_code=400, detail="Question is required in request body")

        preferred_model = map_frontend_model_to_enum(preferred_model_str)
        anon_user_id = request.cookies.get("anon_user_id") or str(uuid.uuid4())

        async def generate_analysis():
            try:
                async for event in analyze_v2_stream_service.stream(
                    ticker=ticker,
                    question=question,
                    use_url_context=use_url_context,
                    deep_analysis=deep_analysis,
                    preferred_model=preferred_model,
                    conversation_id=conversation_id,
                    anon_user_id=anon_user_id,
                    is_disconnected=request.is_disconnected,
                    cache_replay_request=request,
                ):
                    yield json.dumps(event) + "\n\n"
            except asyncio.CancelledError:
                return

        response = StreamingResponse(generate_analysis(), media_type="text/event-stream")
        if not request.cookies.get("anon_user_id"):
            is_production = os.getenv("ENV", "local").lower() == "production"
            response.set_cookie(
                key="anon_user_id",
                value=anon_user_id,
                max_age=86400 * 365,
                httponly=True,
                samesite="None" if is_production else "Lax",
                secure=is_production,
            )
        return response
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Something went wrong. Please try again later.") from exc
