"""Verifies Google ID tokens from the iOS app's OAuth client."""

import os
from functools import lru_cache

import jwt
from jwt import PyJWKClient

from services.auth import LEEWAY_SECONDS, AuthError, TokenClaims

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")


class GoogleAuthNotConfiguredError(Exception):
    """GOOGLE_IOS_CLIENT_ID is missing."""


def get_google_ios_client_id() -> str:
    client_id = os.getenv("GOOGLE_IOS_CLIENT_ID", "")
    if not client_id:
        raise GoogleAuthNotConfiguredError("GOOGLE_IOS_CLIENT_ID missing")
    return client_id


@lru_cache(maxsize=1)
def get_jwks_client() -> PyJWKClient:
    # PyJWKClient caches Google's signing keys and refetches on an unknown `kid`.
    return PyJWKClient(GOOGLE_JWKS_URL)


def verify_google_id_token(token: str, *, client_id: str, jwks_client: PyJWKClient) -> TokenClaims:
    try:
        key = jwks_client.get_signing_key_from_jwt(token).key
        payload = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=list(GOOGLE_ISSUERS),
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
        raise AuthError(str(exc)) from exc

    # Google sends a bool; older tokens used the string "true".
    if payload.get("email_verified") not in (True, "true"):
        raise AuthError("email not verified")
    email = payload.get("email")
    if not isinstance(email, str) or not email:
        raise AuthError("missing email claim")

    return TokenClaims(sub=payload["sub"], email=email, name=payload.get("name"), picture=payload.get("picture"))
