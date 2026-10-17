"""Native app sign-in: swaps a Google ID token for an opaque session token."""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel

from api.deps import get_auth_service
from api.me import MeResponse
from services.auth import AuthError, AuthService, GoogleAuthNotConfiguredError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class GoogleSignInRequest(BaseModel):
    id_token: str


class AuthResponse(BaseModel):
    token: str
    user: MeResponse


@router.post("/google", response_model=AuthResponse)
def sign_in_with_google(
    body: GoogleSignInRequest,
    service: AuthService = Depends(get_auth_service),
) -> AuthResponse:
    try:
        token, user = service.sign_in_with_google(body.id_token)
    except GoogleAuthNotConfiguredError as exc:
        logger.error("Google sign-in called but %s", exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Sign-in not configured")
    except AuthError as exc:
        logger.info("Google sign-in rejected: %s", exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google token")

    return AuthResponse(
        token=token,
        user=MeResponse(id=user.id, email=user.email, name=user.name, avatar_url=user.avatar_url),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    authorization: str | None = Header(default=None),
    service: AuthService = Depends(get_auth_service),
) -> Response:
    """Revokes the bearer session. Always 204, so a retried or stale logout is harmless."""
    service.logout(authorization)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
