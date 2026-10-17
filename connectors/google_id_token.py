"""Verifies Google ID tokens from the iOS app's OAuth client against Google's JWKS."""

import os
from dataclasses import dataclass
from functools import lru_cache

import jwt
from jwt import PyJWKClient

GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
LEEWAY_SECONDS = 30


class GoogleIdTokenNotConfiguredError(Exception):
    """GOOGLE_IOS_CLIENT_ID is missing."""


class GoogleIdTokenInvalidError(Exception):
    """Bad signature, audience, issuer or expiry, or no signing key for the token."""


@dataclass(frozen=True)
class GoogleIdTokenDto:
    sub: str
    email: str | None
    email_verified: bool
    name: str | None
    picture: str | None


@lru_cache(maxsize=1)
def _jwks_client() -> PyJWKClient:
    # PyJWKClient caches Google's signing keys and refetches on an unknown `kid`.
    return PyJWKClient(GOOGLE_JWKS_URL)


class GoogleIdTokenConnector:
    def __init__(self, client_id: str | None = None, jwks_client: PyJWKClient | None = None):
        self._client_id = client_id
        self._jwks_client = jwks_client

    def verify(self, id_token: str) -> GoogleIdTokenDto:
        client_id = self._client_id or os.getenv("GOOGLE_IOS_CLIENT_ID", "")
        if not client_id:
            raise GoogleIdTokenNotConfiguredError("GOOGLE_IOS_CLIENT_ID missing")
        jwks_client = self._jwks_client or _jwks_client()
        try:
            key = jwks_client.get_signing_key_from_jwt(id_token).key
            payload = jwt.decode(
                id_token,
                key,
                algorithms=["RS256"],
                audience=client_id,
                issuer=list(GOOGLE_ISSUERS),
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
            raise GoogleIdTokenInvalidError(str(exc)) from exc

        email = payload.get("email")
        return GoogleIdTokenDto(
            sub=payload["sub"],
            email=email if isinstance(email, str) else None,
            # Google sends a bool; older tokens used the string "true".
            email_verified=payload.get("email_verified") in (True, "true"),
            name=payload.get("name"),
            picture=payload.get("picture"),
        )
