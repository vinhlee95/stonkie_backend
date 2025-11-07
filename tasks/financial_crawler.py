"""
Financial data crawler Celery tasks.
"""

import logging
from typing import Optional

from celery import Task

from celery_app import celery_app

logger = logging.getLogger(__name__)


class CallbackTask(Task):
    """
    Custom task class that adds callback support for task lifecycle events.
    """

    def on_success(self, retval, task_id, args, kwargs):
        """Called when task succeeds."""
        logger.info(f"Task {task_id} completed successfully with result: {retval}")

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Called when task fails."""
        logger.error(f"Task {task_id} failed with error: {exc}")

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        """Called when task is retried."""
        logger.warning(f"Task {task_id} is being retried due to: {exc}")


@celery_app.task(base=CallbackTask, bind=True, name="tasks.test_task", max_retries=3, default_retry_delay=60)
def test_task(self, message: str = "Hello from Celery!") -> dict:
    """
    Simple test task to verify Celery is working correctly.

    Args:
        message: Message to print and return

    Returns:
        dict with status and message
    """
    try:
        logger.info(f"🎉 Test task started with message: {message}")
        print(f"🎉 Test task executing: {message}")

        # Simulate some work
        import time

        time.sleep(2)

        result = {"status": "success", "message": message, "task_id": self.request.id}

        logger.info("✅ Test task completed successfully")
        print(f"✅ Test task completed: {result}")

        return result

    except Exception as e:
        logger.error(f"❌ Test task failed: {e}")
        # Retry the task with exponential backoff
        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))


@celery_app.task(
    base=CallbackTask, bind=True, name="tasks.crawl_financial_data", max_retries=3, default_retry_delay=300
)
def crawl_financial_data_task(
    self, ticker: str, report_type: Optional[str] = None, period_type: Optional[str] = None
) -> dict:
    """
    Celery task to crawl financial data for a company.

    Args:
        ticker: Company ticker symbol
        report_type: Type of financial report (income_statement, balance_sheet, cash_flow)
        period_type: Period type (annually, quarterly)

    Returns:
        dict with status and details
    """
    try:
        logger.info(f"🚀 Starting financial data crawl for {ticker}")
        print(f"🚀 Crawling financial data for {ticker} (report_type={report_type}, period_type={period_type})")

        # TODO: Implement actual crawling logic
        # For now, just simulate the task
        import time

        time.sleep(5)

        result = {
            "status": "success",
            "ticker": ticker.upper(),
            "report_type": report_type,
            "period_type": period_type,
            "task_id": self.request.id,
            "message": f"Successfully crawled data for {ticker}",
        }

        logger.info(f"✅ Financial data crawl completed for {ticker}")
        print(f"✅ Crawl completed for {ticker}: {result}")

        return result

    except Exception as e:
        logger.error(f"❌ Financial data crawl failed for {ticker}: {e}")
        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=60 * (2**self.request.retries))
