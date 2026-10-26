"""LLM extraction of a scraped Yahoo statement table into per-period JSON (gpt-4.1-mini via OpenRouter)."""

import logging
from typing import Any, Optional

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName
from utils.json_extract import extract_json_array

logger = logging.getLogger(__name__)


def extract_statement_periods(prompt: str, agent: MultiAgent | None = None) -> Optional[list[dict[str, Any]]]:
    """The model's JSON array of periods; None when the reply holds no parseable array."""
    agent = agent or MultiAgent(model_name=ModelName.Gpt4Mini)
    try:
        return extract_json_array(agent.generate_text(prompt))
    except ValueError as e:
        logger.warning(f"Statement extraction returned no JSON array: {e}")
        return None
