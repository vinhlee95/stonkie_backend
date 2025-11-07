"""
Test script for Celery annual financial data crawler task.

Usage:
    python test_celery_crawler.py
"""

import sys
from pathlib import Path

from tasks.financial_crawler import crawl_annual_financial_data_task

# Add parent directory to path
current_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(current_dir))


def test_crawl_task():
    """Test the annual financial data crawl task."""

    # Test with a well-known ticker
    ticker = "BA"
    statement_type = "cash_flow"

    print(f"🧪 Testing Celery task for {ticker} - {statement_type}")
    print("=" * 60)

    # Queue the task
    result = crawl_annual_financial_data_task.delay(ticker, statement_type)

    print("✅ Task queued successfully!")
    print(f"   Task ID: {result.id}")
    print(f"   Status: {result.status}")
    print()
    print("To check the task status:")
    print(f"   result = crawl_annual_financial_data_task.AsyncResult('{result.id}')")
    print("   print(result.status)")
    print("   print(result.get(timeout=300))  # Wait up to 5 minutes")
    print()
    print("Watch the Celery worker terminal to see the task executing!")

    # Optionally wait for result
    print("\nWaiting for task to complete (timeout: 5 minutes)...")
    try:
        task_result = result.get(timeout=300)
        print("\n✅ Task completed successfully!")
        print(f"   Result: {task_result}")
    except Exception as e:
        print(f"\n❌ Task failed or timed out: {e}")


if __name__ == "__main__":
    test_crawl_task()
