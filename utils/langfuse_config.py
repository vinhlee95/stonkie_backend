"""Langfuse configuration and utilities for tracing AI interactions."""

import logging
import os
import uuid
from typing import Optional

from dotenv import load_dotenv
from langfuse import Langfuse

load_dotenv()

logger = logging.getLogger(__name__)


class LangfuseConfig:
    """Singleton configuration for Langfuse client."""

    _instance: Optional[Langfuse] = None
    _enabled: bool = False

    @classmethod
    def get_client(cls) -> Optional[Langfuse]:
        """
        Get or create the Langfuse client instance.

        Returns:
            Langfuse client instance or None if not configured
        """
        if cls._instance is None:
            cls._initialize()
        return cls._instance if cls._enabled else None

    @classmethod
    def _initialize(cls) -> None:
        """Initialize the Langfuse client with environment variables."""
        public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
        secret_key = os.getenv("LANGFUSE_SECRET_KEY")
        host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
        environment = os.getenv("ENV", "local")  # Default to "local" if not set

        if public_key and secret_key:
            try:
                cls._instance = Langfuse(
                    public_key=public_key,
                    secret_key=secret_key,
                    host=host,
                    environment=environment,
                )
                cls._enabled = True
                logger.info(f"Langfuse client initialized successfully (environment: {environment})")
            except Exception as e:
                logger.error(f"Failed to initialize Langfuse client: {e}")
                cls._enabled = False
        else:
            logger.warning(
                "Langfuse not configured. Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY environment variables to enable tracing."
            )
            cls._enabled = False

    @classmethod
    def is_enabled(cls) -> bool:
        """Check if Langfuse is enabled and configured."""
        if cls._instance is None:
            cls._initialize()
        return cls._enabled


def extract_or_generate_session_id(headers: dict) -> str:
    """
    Extract session ID from request headers or generate a new one.

    Args:
        headers: Request headers dictionary

    Returns:
        Session ID string
    """
    # Try to extract from common header names
    session_id = headers.get("x-session-id") or headers.get("x-stonkie-session-id")

    if not session_id:
        # Generate a new session ID
        session_id = str(uuid.uuid4())
        logger.debug(f"Generated new session ID: {session_id}")
    else:
        logger.debug(f"Using existing session ID: {session_id}")

    return session_id


def get_user_id_from_headers(headers: dict) -> Optional[str]:
    """
    Extract user ID from request headers if available.

    Args:
        headers: Request headers dictionary

    Returns:
        User ID string or None
    """
    return headers.get("x-user-id") or headers.get("x-stonkie-user-id")
