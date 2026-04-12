"""SSE thinking_status metadata shared across analyze flows (company, ETF, filings)."""

from enum import StrEnum
from typing import Optional


class AnalysisPhase(StrEnum):
    CLASSIFY = "classify"
    DATA_FETCH = "data_fetch"
    SEARCH = "search"
    ANALYZE = "analyze"
    ENRICH = "enrich"


def thinking_status(
    body: str,
    *,
    phase: AnalysisPhase | str,
    step: int,
    total_steps: Optional[int] = None,
) -> dict:
    event: dict = {
        "type": "thinking_status",
        "body": body,
        "phase": phase,
        "step": step,
    }
    if total_steps is not None:
        event["total_steps"] = total_steps
    return event
