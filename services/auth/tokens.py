import os
from dataclasses import dataclass

import jwt

from connectors.google_id_token import GoogleIdTokenDto

TOKEN_ISSUER = "stonkie-web"
TOKEN_AUDIENCE = "stonkie-api"
LEEWAY_SECONDS = 30
MIN_SECRET_BYTES = 32


class AuthError(Exception):
    """Token missing, malformed, expired, or otherwise invalid."""


class AuthNotConfiguredError(Exception):
    """BACKEND_JWT_SECRET is missing or too short."""


class GoogleAuthNotConfiguredError(Exception):
    """GOOGLE_IOS_CLIENT_ID is missing."""


@dataclass(frozen=True)
class TokenClaims:
    sub: str
    email: str
    name: str | None = None
    picture: str | None = None


def get_backend_jwt_secret() -> str:
    secret = os.getenv("BACKEND_JWT_SECRET", "")
    if len(secret.encode()) < MIN_SECRET_BYTES:
        raise AuthNotConfiguredError("BACKEND_JWT_SECRET missing or shorter than 32 bytes")
    return secret


def verify_backend_token(token: str, secret: str) -> TokenClaims:
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=TOKEN_AUDIENCE,
            issuer=TOKEN_ISSUER,
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthError(str(exc)) from exc

    email = payload.get("email")
    if not isinstance(email, str) or not email:
        raise AuthError("missing email claim")

    return TokenClaims(
        sub=payload["sub"],
        email=email,
        name=payload.get("name"),
        picture=payload.get("picture"),
    )


def google_claims(token: GoogleIdTokenDto) -> TokenClaims:
    """Claims from a signature-verified Google ID token; only verified emails sign in."""
    if not token.email_verified:
        raise AuthError("email not verified")
    if not token.email:
        raise AuthError("missing email claim")
    return TokenClaims(sub=token.sub, email=token.email, name=token.name, picture=token.picture)
