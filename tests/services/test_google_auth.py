import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from services.auth import AuthError, TokenClaims
from services.google_auth import (
    GoogleAuthNotConfiguredError,
    get_google_ios_client_id,
    verify_google_id_token,
)

CLIENT_ID = "ios-client"


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJwks:
    def __init__(self, public_key):
        self.public_key = public_key

    def get_signing_key_from_jwt(self, token):
        return SimpleNamespace(key=self.public_key)


@pytest.fixture()
def jwks(signing_key):
    return FakeJwks(signing_key.public_key())


def google_token(signing_key, **overrides) -> str:
    now = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "google-123",
        "email": "a@example.com",
        "email_verified": True,
        "name": "Ann",
        "picture": "https://img/x.png",
        "iat": now,
        "exp": now + 300,
    }
    payload.update(overrides)
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, signing_key, algorithm="RS256", headers={"kid": "k1"})


def verify(token, jwks):
    return verify_google_id_token(token, client_id=CLIENT_ID, jwks_client=jwks)


def test_valid_token_returns_claims(signing_key, jwks):
    assert verify(google_token(signing_key), jwks) == TokenClaims(
        sub="google-123", email="a@example.com", name="Ann", picture="https://img/x.png"
    )


def test_bare_issuer_accepted(signing_key, jwks):
    assert verify(google_token(signing_key, iss="accounts.google.com"), jwks).sub == "google-123"


def test_string_email_verified_accepted(signing_key, jwks):
    assert verify(google_token(signing_key, email_verified="true"), jwks).email == "a@example.com"


@pytest.mark.parametrize(
    "override",
    [
        {"aud": "web-client"},
        {"iss": "https://evil.example.com"},
        {"exp": int(time.time()) - 600},
        {"email_verified": False},
        {"email_verified": None},
        {"email": None},
        {"sub": None},
    ],
)
def test_rejected(signing_key, jwks, override):
    with pytest.raises(AuthError):
        verify(google_token(signing_key, **override), jwks)


def test_wrong_signing_key_rejected(signing_key):
    other = FakeJwks(rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key())
    with pytest.raises(AuthError):
        verify(google_token(signing_key), other)


def test_jwks_lookup_failure_rejected(signing_key):
    class FailingJwks:
        def get_signing_key_from_jwt(self, token):
            raise jwt.PyJWKClientError("no matching key")

    with pytest.raises(AuthError):
        verify(google_token(signing_key), FailingJwks())


def test_missing_client_id_env_raises_not_configured(monkeypatch):
    monkeypatch.delenv("GOOGLE_IOS_CLIENT_ID", raising=False)
    with pytest.raises(GoogleAuthNotConfiguredError):
        get_google_ios_client_id()


def test_client_id_read_from_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_IOS_CLIENT_ID", CLIENT_ID)
    assert get_google_ios_client_id() == CLIENT_ID
