import time

import jwt
import pytest

from core.auth import (
    AuthError,
    AuthNotConfiguredError,
    TokenClaims,
    get_backend_jwt_secret,
    verify_backend_token,
)

SECRET = "s" * 32


def make_token(secret: str = SECRET, **overrides) -> str:
    now = int(time.time())
    payload = {
        "sub": "google-123",
        "email": "a@example.com",
        "name": "Ann",
        "picture": "https://img/x.png",
        "iss": "stonkie-web",
        "aud": "stonkie-api",
        "iat": now,
        "exp": now + 300,
    }
    payload.update(overrides)
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, secret, algorithm="HS256")


def test_valid_token_returns_claims():
    assert verify_backend_token(make_token(), SECRET) == TokenClaims(
        sub="google-123", email="a@example.com", name="Ann", picture="https://img/x.png"
    )


def test_token_without_name_and_picture_is_accepted():
    claims = verify_backend_token(make_token(name=None, picture=None), SECRET)
    assert claims.name is None
    assert claims.picture is None


def test_iat_slightly_in_future_is_accepted():
    now = int(time.time())
    verify_backend_token(make_token(iat=now + 10, exp=now + 310), SECRET)


def test_expired_token_rejected():
    now = int(time.time())
    with pytest.raises(AuthError):
        verify_backend_token(make_token(iat=now - 400, exp=now - 100), SECRET)


def test_bad_signature_rejected():
    with pytest.raises(AuthError):
        verify_backend_token(make_token(secret="x" * 32), SECRET)


@pytest.mark.parametrize(
    "override", [{"aud": "other"}, {"iss": "other"}, {"sub": None}, {"email": None}, {"email": ""}]
)
def test_invalid_claims_rejected(override):
    with pytest.raises(AuthError):
        verify_backend_token(make_token(**override), SECRET)


def test_garbage_token_rejected():
    with pytest.raises(AuthError):
        verify_backend_token("not-a-jwt", SECRET)


def test_get_secret_requires_32_bytes(monkeypatch):
    monkeypatch.setenv("BACKEND_JWT_SECRET", "short")
    with pytest.raises(AuthNotConfiguredError):
        get_backend_jwt_secret()
    monkeypatch.delenv("BACKEND_JWT_SECRET")
    with pytest.raises(AuthNotConfiguredError):
        get_backend_jwt_secret()
    monkeypatch.setenv("BACKEND_JWT_SECRET", SECRET)
    assert get_backend_jwt_secret() == SECRET
