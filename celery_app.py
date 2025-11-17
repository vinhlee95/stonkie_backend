"""
Celery application configuration for background task processing.
"""

import logging
import os

from celery import Celery
from dotenv import load_dotenv

from utils.logging import setup_local_logging, setup_production_logging

load_dotenv()

# Setup logging based on environment
environment = os.getenv("ENV", "local").lower()
log_level = os.getenv("LOG_LEVEL", "INFO").upper()

if environment == "local":
    setup_local_logging(log_level)
else:
    setup_production_logging(log_level)

logger = logging.getLogger(__name__)

# Get Redis URL from environment, default to localhost for development
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Initialize Celery app
celery_app = Celery(
    "stonkie",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["tasks.financial_crawler"],  # Auto-discover tasks from this module
)

# Celery configuration
celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Broker connection
    broker_connection_retry_on_startup=True,  # Retry broker connection on startup
    # Task execution
    task_track_started=True,
    task_time_limit=300,  # 5 minutes max per task
    task_soft_time_limit=240,  # 4 minutes soft limit
    # Result backend
    result_expires=300,  # Results expire after 5 minutes instead of 1 hour (saves memory)
    result_extended=True,
    # Retry policy
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Worker - Aggressive memory optimization
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1,  # Restart worker after each task to free memory (Playwright browsers)
    worker_disable_rate_limits=True,  # Remove rate limiting overhead
    # Pool configuration for minimal memory usage
    worker_pool_restarts=True,  # Enable pool restarts
    worker_pool="solo",  # Use solo pool (single process) for minimal overhead
    # Autoscaling - workers will shut down when idle
    worker_autoscaler="celery.worker.autoscale:Autoscaler",
    # Connection settings
    broker_pool_limit=1,  # Minimize broker connections
    broker_connection_retry=False,  # Don't retry connections (fail fast)
    # Task result settings for memory efficiency
    task_store_eager_result=False,  # Don't store eager results
)

logger.info(f"Celery configured with broker: {REDIS_URL}")
