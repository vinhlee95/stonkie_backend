from datetime import UTC, datetime

import pytest

from connectors.google_id_token import GoogleIdTokenDto, GoogleIdTokenInvalidError, GoogleIdTokenNotConfiguredError
from connectors.user import UserDto
from services.auth import AuthError, AuthService, GoogleAuthNotConfiguredError

DTO = GoogleIdTokenDto(
    sub="google-123", email="a@example.com", email_verified=True, name="Ann", picture="https://img/a.png"
)


class FakeGoogle:
    def __init__(self, result: GoogleIdTokenDto | Exception = DTO):
        self.result = result
        self.calls: list[str] = []

    def verify(self, id_token: str) -> GoogleIdTokenDto:
        self.calls.append(id_token)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeUsers:
    def __init__(self):
        self.calls: list[dict] = []

    def upsert(self, **kwargs) -> UserDto:
        self.calls.append(kwargs)
        now = datetime.now(UTC)
        return UserDto(id="u-1", created_at=now, last_seen_at=now, **kwargs)


class FakeSessions:
    def __init__(self):
        self.created: list[str] = []
        self.deleted: list[str] = []

    def create(self, user_id: str) -> str:
        self.created.append(user_id)
        return "session-token"

    def delete(self, token: str) -> None:
        self.deleted.append(token)


def make_service(google=None, users=None, sessions=None) -> AuthService:
    return AuthService(users=users or FakeUsers(), sessions=sessions or FakeSessions(), google=google or FakeGoogle())


def test_sign_in_upserts_user_and_creates_session():
    users, sessions, google = FakeUsers(), FakeSessions(), FakeGoogle()

    token, user = make_service(google, users, sessions).sign_in_with_google("id-token")

    assert token == "session-token"
    assert user.id == "u-1"
    assert google.calls == ["id-token"]
    assert users.calls == [
        {"google_sub": "google-123", "email": "a@example.com", "name": "Ann", "avatar_url": "https://img/a.png"}
    ]
    assert sessions.created == ["u-1"]


@pytest.mark.parametrize(
    "dto",
    [
        GoogleIdTokenDto(sub="g", email="a@example.com", email_verified=False, name=None, picture=None),
        GoogleIdTokenDto(sub="g", email=None, email_verified=True, name=None, picture=None),
        GoogleIdTokenDto(sub="g", email="", email_verified=True, name=None, picture=None),
    ],
)
def test_sign_in_rejects_unverified_or_missing_email(dto):
    users, sessions = FakeUsers(), FakeSessions()

    with pytest.raises(AuthError):
        make_service(FakeGoogle(dto), users, sessions).sign_in_with_google("id-token")
    assert users.calls == []
    assert sessions.created == []


def test_sign_in_invalid_token_raises_auth_error():
    with pytest.raises(AuthError):
        make_service(FakeGoogle(GoogleIdTokenInvalidError("bad"))).sign_in_with_google("id-token")


def test_sign_in_not_configured():
    with pytest.raises(GoogleAuthNotConfiguredError):
        make_service(FakeGoogle(GoogleIdTokenNotConfiguredError("missing"))).sign_in_with_google("id-token")


@pytest.mark.parametrize(
    "header, deleted",
    [("Bearer abc", ["abc"]), ("bearer abc ", ["abc"]), (None, []), ("", []), ("Bearer ", []), ("Basic abc", [])],
)
def test_logout_deletes_only_bearer_tokens(header, deleted):
    sessions = FakeSessions()

    make_service(sessions=sessions).logout(header)

    assert sessions.deleted == deleted
