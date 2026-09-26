import logging

from fastapi import Depends, Header, HTTPException, status

from connectors.user import UserConnector, UserDto
from core.auth import AuthError, AuthNotConfiguredError, get_backend_jwt_secret, verify_backend_token

logger = logging.getLogger(__name__)


def get_user_connector() -> UserConnector:
    return UserConnector()


def _unauthorized(reason: str) -> HTTPException:
    logger.debug("Auth rejected: %s", reason)
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    authorization: str | None = Header(default=None),
    users: UserConnector = Depends(get_user_connector),
) -> UserDto:
    try:
        secret = get_backend_jwt_secret()
    except AuthNotConfiguredError as exc:
        logger.error("Protected route called but %s", exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Authentication not configured")

    scheme, _, token = (authorization or "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized("missing bearer token")

    try:
        claims = verify_backend_token(token, secret)
    except AuthError as exc:
        raise _unauthorized(str(exc))

    return users.upsert(google_sub=claims.sub, email=claims.email, name=claims.name, avatar_url=claims.picture)
