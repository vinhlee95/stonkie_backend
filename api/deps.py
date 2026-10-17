import logging

from fastapi import Depends, Header, HTTPException, status

from connectors.user import UserDto
from services.auth import AuthError, AuthNotConfiguredError, AuthService

logger = logging.getLogger(__name__)


def get_auth_service() -> AuthService:
    return AuthService()


def get_current_user(
    authorization: str | None = Header(default=None),
    service: AuthService = Depends(get_auth_service),
) -> UserDto:
    """FastAPI dependency: maps auth service errors to 401/503."""
    try:
        return service.authenticate(authorization)
    except AuthNotConfiguredError as exc:
        logger.error("Protected route called but %s", exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Authentication not configured")
    except AuthError as exc:
        logger.debug("Auth rejected: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
