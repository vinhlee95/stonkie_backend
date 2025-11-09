"""
Redis cache connector for task state management.
"""

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

import redis
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Redis client singleton
redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))

TaskStatus = Literal["pending", "running", "completed", "failed"]


@dataclass(frozen=True)
class TaskDispatchDecision:
    """Decision on whether to dispatch a new crawl task."""

    can_dispatch: bool
    reason: str
    existing_task_id: Optional[str] = None
    existing_status: Optional[TaskStatus] = None
    existing_state: Optional[dict] = None


def get_task_state_key(ticker: str, report_type: str, period_type: str = "annually") -> str:
    """Generate Redis key for task state."""
    return f"task_state:{ticker.upper()}:{report_type}:{period_type}"


def get_task_state(ticker: str, report_type: str, period_type: str = "annually") -> Optional[dict]:
    """
    Get current task state from Redis.

    Args:
        ticker: Company ticker symbol
        report_type: Type of report (income_statement, balance_sheet, cash_flow)
        period_type: Period type (annually, quarterly)

    Returns:
        dict with task state or None if not found
    """
    key = get_task_state_key(ticker, report_type, period_type)
    state_json = redis_client.get(key)

    if state_json:
        return json.loads(state_json)

    return None


def set_task_state(
    ticker: str,
    report_type: str,
    status: TaskStatus,
    task_id: str,
    period_type: str = "annually",
    error: Optional[str] = None,
) -> None:
    """
    Set task state in Redis.

    Args:
        ticker: Company ticker symbol
        report_type: Type of report
        status: Task status (pending, running, completed, failed)
        task_id: Celery task ID
        period_type: Period type
        error: Error message if failed
    """
    key = get_task_state_key(ticker, report_type, period_type)
    now = datetime.utcnow().isoformat()

    # Get existing state to preserve created_at
    existing_state = get_task_state(ticker, report_type, period_type)
    created_at = existing_state["created_at"] if existing_state else now

    state = {
        "task_id": task_id,
        "status": status,
        "ticker": ticker.upper(),
        "report_type": report_type,
        "period_type": period_type,
        "created_at": created_at,
        "updated_at": now,
    }

    if error:
        state["error"] = error

    # Set TTL based on status
    if status == "completed":
        ttl = 300  # 5 minutes - short TTL since data is in DB
    elif status == "failed":
        ttl = 3600  # 1 hour - allow some time before retry
    else:
        ttl = 900  # 15 minutes for pending/running

    redis_client.setex(key, ttl, json.dumps(state))
    logger.info(f"Set task state: {ticker} - {report_type} -> {status} (TTL: {ttl}s)")


def can_dispatch_task(ticker: str, report_type: str, period_type: str = "annually") -> TaskDispatchDecision:
    """
    Check if a new task can be dispatched.

    Args:
        ticker: Company ticker symbol
        report_type: Type of report
        period_type: Period type

    Returns:
        TaskDispatchDecision with decision and details
    """
    state = get_task_state(ticker, report_type, period_type)

    if not state:
        # No existing task, can dispatch
        return TaskDispatchDecision(can_dispatch=True, reason="No existing task found")

    status = state.get("status")
    task_id = state.get("task_id")

    if status in ["pending", "running"]:
        # Task already in progress
        logger.info(f"Task already {status} for {ticker} - {report_type}, task_id: {task_id}")
        return TaskDispatchDecision(
            can_dispatch=False,
            reason=f"Task already {status}",
            existing_task_id=task_id,
            existing_status=status,
            existing_state=state,
        )

    if status == "completed":
        # Task completed, no need to dispatch again
        logger.info(f"Task already completed for {ticker} - {report_type}")
        return TaskDispatchDecision(
            can_dispatch=False,
            reason="Task already completed",
            existing_task_id=task_id,
            existing_status=status,
            existing_state=state,
        )

    if status == "failed":
        # Failed task, can retry
        logger.info(f"Previous task failed for {ticker} - {report_type}, allowing retry")
        return TaskDispatchDecision(
            can_dispatch=True,
            reason="Previous task failed, retrying",
            existing_task_id=task_id,
            existing_status=status,
            existing_state=state,
        )

    # Unknown status, allow dispatch
    return TaskDispatchDecision(can_dispatch=True, reason=f"Unknown status '{status}', allowing dispatch")
