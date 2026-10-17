"""Native app sign-in: swaps a Google ID token for an opaque session token."""

import logging
from collections.abc import Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel

from api.deps import get_session_connector, get_user_connector
from api.me import MeResponse
from connectors.session import SessionConnector
from connectors.user import UserConnector
from services.auth import AuthError, TokenClaims
from services.google_auth import (
    GoogleAuthNotConfiguredError,
    get_google_ios_client_id,
    get_jwks_client,
    verify_google_id_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class GoogleSignInRequest(BaseModel):
    id_token: str


class AuthResponse(BaseModel):
    token: str
    user: MeResponse


def get_google_verifier() -> Callable[[str], TokenClaims]:
    try:
        client_id = get_google_ios_client_id()
    except GoogleAuthNotConfiguredError as exc:
        logger.error("Google sign-in called but %s", exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sign-in not configured")
    jwks_client = get_jwks_client()
    return lambda id_token: verify_google_id_token(id_token, client_id=client_id, jwks_client=jwks_client)


@router.post("/google", response_model=AuthResponse)
def sign_in_with_google(
    body: GoogleSignInRequest,
    verify: Callable[[str], TokenClaims] = Depends(get_google_verifier),
    users: UserConnector = Depends(get_user_connector),
    sessions: SessionConnector = Depends(get_session_connector),
) -> AuthResponse:
    try:
        claims = verify(body.id_token)
    except AuthError as exc:
        logger.info("Google sign-in rejected: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google token")

    user = users.upsert(google_sub=claims.sub, email=claims.email, name=claims.name, avatar_url=claims.picture)
    return AuthResponse(
        token=sessions.create(user.id),
        user=MeResponse(id=user.id, email=user.email, name=user.name, avatar_url=user.avatar_url),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    authorization: str | None = Header(default=None),
    sessions: SessionConnector = Depends(get_session_connector),
) -> Response:
    """Revokes the bearer session. Always 204, so a retried or stale logout is harmless."""
    scheme, _, token = (authorization or "").partition(" ")
    token = token.strip()
    if scheme.lower() == "bearer" and token:
        sessions.delete(token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
