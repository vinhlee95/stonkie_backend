"""LLM extraction of a scraped Yahoo statement table into per-period JSON (gpt-4.1-mini via OpenRouter)."""

import logging
from typing import Any, Optional

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from utils.json_extract import extract_json_array

logger = logging.getLogger(__name__)

EXTRACTION_MODEL = ModelName.Gpt4Mini
# An expanded statement is ~80 rows x 5-6 periods of JSON; the shared 8192 default can cut it off.
EXTRACTION_MAX_TOKENS = 32768


def extract_statement_periods(prompt: str, agent: MultiAgent | None = None) -> Optional[list[dict[str, Any]]]:
    """The model's JSON array of periods; None when the reply holds no parseable array."""
    agent = agent or MultiAgent(model_name=EXTRACTION_MODEL)
    reply = agent.generate_text(prompt, max_tokens=EXTRACTION_MAX_TOKENS)
    try:
        return extract_json_array(reply)
    except ValueError as e:
        logger.warning(f"Statement extraction returned no JSON array ({len(reply)} chars, may be cut off): {e}")
        return None
