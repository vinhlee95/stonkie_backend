"""HTTP route for market recap analyze chat."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ai_models.model_mapper import map_frontend_model_to_enum
from connectors.database import get_db
from models.market_recap import MarketRecap
from services.analyze_retrieval.schemas import BraveRetrievalError
from services.recap_analyze import RecapAnalyzeStreamService

router = APIRouter()
recap_analyze_stream_service = RecapAnalyzeStreamService()
logger = logging.getLogger(__name__)


@router.post("/api/recaps/{recap_id}/analyze")
async def analyze_recap(recap_id: int, request: Request, db: Session = Depends(get_db)) -> StreamingResponse:
    try:
        body = await request.json()
        question = body.get("question")
        preferred_model_str = body.get("preferredModel", "fastest")
        conversation_id = body.get("conversationId")
        debug_prompt_context = body.get("debugPromptContext", False)

        if not question:
            raise HTTPException(status_code=400, detail="Question is required in request body")

        recap = db.query(MarketRecap).filter(MarketRecap.id == recap_id).one_or_none()
        if recap is None:
            raise HTTPException(status_code=404, detail="Recap not found")

        preferred_model = map_frontend_model_to_enum(preferred_model_str)
        anon_user_id = request.cookies.get("anon_user_id") or str(uuid.uuid4())

        async def generate_analysis():
            try:
                async for event in recap_analyze_stream_service.stream(
                    recap=recap,
                    question=question,
                    preferred_model=preferred_model,
                    conversation_id=conversation_id,
                    anon_user_id=anon_user_id,
                    is_disconnected=request.is_disconnected,
                    debug_prompt_context=debug_prompt_context,
                ):
                    yield json.dumps(event) + "\n\n"
            except BraveRetrievalError:
                logger.exception("recap analyze retrieval failed", extra={"recap_id": recap_id})
                yield json.dumps({"type": "error", "code": "retrieval_failed", "body": "Retrieval failed"}) + "\n\n"
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
