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


class ThinkingProgress:
    """Numbers one request's thinking_status events 1, 2, 3, ...

    No total_steps: how many statuses a request sends depends on its path and on what search returns.
    """

    def __init__(self) -> None:
        self._step = 0

    def status(self, body: str, *, phase: AnalysisPhase | str) -> dict:
        self._step += 1
        return thinking_status(body, phase=phase, step=self._step)

    def renumber(self, event: dict) -> dict:
        """Put a thinking_status from a nested emitter (a v2 handler) onto this request's step sequence."""
        if event.get("type") != "thinking_status":
            return event
        self._step += 1
        renumbered = {key: value for key, value in event.items() if key != "total_steps"}
        renumbered["step"] = self._step
        return renumbered
