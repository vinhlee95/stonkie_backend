"""
Celery application configuration for background task processing.
"""

import logging
import os
import sys

from celery import Celery
from dotenv import load_dotenv

load_dotenv()

# Configure logging for Celery
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
numeric_level = getattr(logging, log_level, logging.INFO)

logging.basicConfig(
    level=numeric_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],  # Force stdout instead of stderr
)

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
    # Task execution
    task_track_started=True,
    task_time_limit=300,  # 5 minutes max per task
    task_soft_time_limit=240,  # 4 minutes soft limit
    # Result backend
    result_expires=3600,  # Results expire after 1 hour
    result_extended=True,
    # Retry policy
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Worker
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1,  # Restart worker after each task to free memory (Playwright browsers)
    # Pool configuration
    worker_pool_restarts=True,  # Enable pool restarts
)

logger.info(f"Celery configured with broker: {REDIS_URL}")
