"""
Custom logging utilities for structured logging in production.
"""

import json
import logging
import sys
from typing import Any, Dict


class JSONFormatter(logging.Formatter):
    """
    Custom JSON formatter that extracts 'extra' fields and puts them in metadata.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Base log data
        log_data: Dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add metadata from extra fields
        metadata = {}
        for key, value in record.__dict__.items():
            # Skip standard logging attributes
            if key not in (
                "name",
                "msg",
                "args",
                "levelname",
                "levelno",
                "pathname",
                "filename",
                "module",
                "lineno",
                "funcName",
                "created",
                "msecs",
                "relativeCreated",
                "thread",
                "threadName",
                "processName",
                "process",
                "exc_info",
                "exc_text",
                "stack_info",
                "getMessage",
                "message",
            ):
                metadata[key] = value

        # Add metadata only if it exists
        if metadata:
            log_data["metadata"] = metadata

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data, default=str)


def setup_production_logging(log_level: str = "INFO") -> None:
    """
    Set up JSON logging for production with metadata support.
    """
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Create JSON formatter
    formatter = JSONFormatter()

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add JSON handler for stdout
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)


def setup_local_logging(log_level: str = "INFO") -> None:
    """
    Set up human-readable logging for local development.
    """
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,  # Override any existing configuration
    )
