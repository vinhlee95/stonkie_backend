import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from connectors.google_id_token import (
    GoogleIdTokenConnector,
    GoogleIdTokenDto,
    GoogleIdTokenInvalidError,
    GoogleIdTokenNotConfiguredError,
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
    return GoogleIdTokenConnector(client_id=CLIENT_ID, jwks_client=jwks).verify(token)


def test_valid_token_returns_dto(signing_key, jwks):
    assert verify(google_token(signing_key), jwks) == GoogleIdTokenDto(
        sub="google-123", email="a@example.com", email_verified=True, name="Ann", picture="https://img/x.png"
    )


def test_bare_issuer_accepted(signing_key, jwks):
    assert verify(google_token(signing_key, iss="accounts.google.com"), jwks).sub == "google-123"


@pytest.mark.parametrize("raw, expected", [(True, True), ("true", True), (False, False), (None, False)])
def test_email_verified_normalized(signing_key, jwks, raw, expected):
    # Google sends a bool; older tokens used the string "true".
    assert verify(google_token(signing_key, email_verified=raw), jwks).email_verified is expected


def test_missing_email_is_none(signing_key, jwks):
    assert verify(google_token(signing_key, email=None), jwks).email is None


@pytest.mark.parametrize(
    "override",
    [
        {"aud": "web-client"},
        {"iss": "https://evil.example.com"},
        {"exp": int(time.time()) - 600},
        {"sub": None},
    ],
)
def test_rejected(signing_key, jwks, override):
    with pytest.raises(GoogleIdTokenInvalidError):
        verify(google_token(signing_key, **override), jwks)


def test_wrong_signing_key_rejected(signing_key):
    other = FakeJwks(rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key())
    with pytest.raises(GoogleIdTokenInvalidError):
        verify(google_token(signing_key), other)


def test_jwks_lookup_failure_rejected(signing_key):
    class FailingJwks:
        def get_signing_key_from_jwt(self, token):
            raise jwt.PyJWKClientError("no matching key")

    with pytest.raises(GoogleIdTokenInvalidError):
        verify(google_token(signing_key), FailingJwks())


def test_missing_client_id_env_raises_not_configured(monkeypatch, signing_key, jwks):
    monkeypatch.delenv("GOOGLE_IOS_CLIENT_ID", raising=False)
    with pytest.raises(GoogleIdTokenNotConfiguredError):
        GoogleIdTokenConnector(jwks_client=jwks).verify(google_token(signing_key))


def test_client_id_read_from_env(monkeypatch, signing_key, jwks):
    monkeypatch.setenv("GOOGLE_IOS_CLIENT_ID", CLIENT_ID)
    assert GoogleIdTokenConnector(jwks_client=jwks).verify(google_token(signing_key)).sub == "google-123"
