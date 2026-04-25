import logging
import uuid
from typing import Any


def new_run_id() -> str:
    return uuid.uuid4().hex


def log_event(logger: logging.Logger, event: str, fields: dict[str, Any]) -> None:
    logger.info(event, extra={"event": event, "fields": fields})
