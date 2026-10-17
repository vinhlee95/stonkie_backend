import time
from datetime import UTC, datetime

import jwt
import pytest

from connectors.user import UserDto
from services.auth import (
    AuthError,
    AuthNotConfiguredError,
    TokenClaims,
    authenticate_bearer,
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


class FakeUserConnector:
    def __init__(self):
        self.calls: list[dict] = []

    def upsert(self, **kwargs) -> UserDto:
        self.calls.append(kwargs)
        now = datetime.now(UTC)
        return UserDto(id="u-1", created_at=now, last_seen_at=now, **kwargs)


class FakeSessionConnector:
    def __init__(self, user: UserDto | None = None):
        self.user = user
        self.lookups: list[str] = []

    def user_for(self, token: str) -> UserDto | None:
        self.lookups.append(token)
        return self.user


@pytest.fixture()
def secret_env(monkeypatch):
    monkeypatch.setenv("BACKEND_JWT_SECRET", SECRET)


def test_authenticate_bearer_upserts_user_from_claims(secret_env):
    users = FakeUserConnector()

    user = authenticate_bearer(f"Bearer {make_token()}", users=users)

    assert user.id == "u-1"
    assert users.calls == [
        {"google_sub": "google-123", "email": "a@example.com", "name": "Ann", "avatar_url": "https://img/x.png"}
    ]


def test_authenticate_bearer_accepts_lowercase_scheme(secret_env):
    assert authenticate_bearer(f"bearer {make_token()}", users=FakeUserConnector()).id == "u-1"


@pytest.mark.parametrize("header", [None, "", "Basic abc", "Bearer ", "Bearer not-a-jwt"])
def test_authenticate_bearer_rejects_bad_header(secret_env, header):
    users = FakeUserConnector()

    with pytest.raises(AuthError):
        authenticate_bearer(header, users=users, sessions=FakeSessionConnector())
    assert users.calls == []


def test_authenticate_bearer_requires_configured_secret(monkeypatch):
    monkeypatch.delenv("BACKEND_JWT_SECRET", raising=False)

    with pytest.raises(AuthNotConfiguredError):
        authenticate_bearer(f"Bearer {make_token()}", users=FakeUserConnector())


def test_opaque_token_uses_session_lookup(monkeypatch):
    # Sessions work even when the web JWT secret isn't configured.
    monkeypatch.delenv("BACKEND_JWT_SECRET", raising=False)
    now = datetime.now(UTC)
    session_user = UserDto(
        id="u-9", google_sub="g", email="a@example.com", name=None, avatar_url=None, created_at=now, last_seen_at=now
    )
    sessions = FakeSessionConnector(session_user)
    users = FakeUserConnector()

    assert authenticate_bearer("Bearer opaque-token", users=users, sessions=sessions) == session_user
    assert sessions.lookups == ["opaque-token"]
    assert users.calls == []


def test_opaque_token_unknown_raises_auth_error():
    with pytest.raises(AuthError):
        authenticate_bearer("Bearer opaque-token", users=FakeUserConnector(), sessions=FakeSessionConnector())


def test_jwt_shaped_token_does_not_hit_sessions(secret_env):
    sessions = FakeSessionConnector()

    authenticate_bearer(f"Bearer {make_token()}", users=FakeUserConnector(), sessions=sessions)

    assert sessions.lookups == []
