from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from connectors import session as session_connector_module
from connectors import user as user_connector_module
from connectors.session import SESSION_TTL, SessionConnector, hash_token
from connectors.user import UserConnector


@pytest.fixture()
def factory(test_engine, db_session, monkeypatch):
    # db_session is requested for its teardown (truncates users, cascading to sessions).
    session_local = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(user_connector_module, "SessionLocal", session_local)
    monkeypatch.setattr(session_connector_module, "SessionLocal", session_local)
    return session_local


@pytest.fixture()
def user(factory):
    return UserConnector().upsert(google_sub="g-1", email="a@example.com", name="Ann", avatar_url=None)


@pytest.fixture()
def sessions(factory):
    return SessionConnector()


def _set(test_engine, token: str, **columns) -> None:
    assignments = ", ".join(f"{name} = :{name}" for name in columns)
    with test_engine.begin() as connection:
        connection.execute(
            text(f"UPDATE user_sessions SET {assignments} WHERE token_hash = :hash"),
            {**columns, "hash": hash_token(token)},
        )


def _row(test_engine, token: str):
    with test_engine.connect() as connection:
        return connection.execute(
            text("SELECT token_hash, last_used_at, expires_at FROM user_sessions WHERE token_hash = :hash"),
            {"hash": hash_token(token)},
        ).one_or_none()


def test_create_then_user_for_returns_user(sessions, user):
    token = sessions.create(user.id)

    found = sessions.user_for(token)

    assert found is not None
    assert (found.id, found.email) == (user.id, "a@example.com")


def test_tokens_are_long_and_unique(sessions, user):
    first, second = sessions.create(user.id), sessions.create(user.id)

    assert first != second
    assert len(first) >= 40


def test_raw_token_not_stored(sessions, user, test_engine):
    token = sessions.create(user.id)

    with test_engine.connect() as connection:
        stored = connection.execute(text("SELECT token_hash FROM user_sessions")).scalars().all()
    assert stored == [hash_token(token)]
    assert token not in stored


def test_new_session_expires_in_90_days(sessions, user, test_engine):
    token = sessions.create(user.id)

    expires_at = _row(test_engine, token).expires_at
    assert abs(expires_at - (datetime.now(UTC) + SESSION_TTL)) < timedelta(minutes=1)


def test_unknown_token_returns_none(sessions, user):
    sessions.create(user.id)

    assert sessions.user_for("not-a-real-token") is None


def test_expired_session_returns_none_and_is_deleted(sessions, user, test_engine):
    token = sessions.create(user.id)
    _set(test_engine, token, expires_at=datetime.now(UTC) - timedelta(seconds=1))

    assert sessions.user_for(token) is None
    assert _row(test_engine, token) is None


def test_slides_expiry_after_a_day(sessions, user, test_engine):
    token = sessions.create(user.id)
    two_days_ago = datetime.now(UTC) - timedelta(days=2)
    _set(test_engine, token, last_used_at=two_days_ago, expires_at=two_days_ago + SESSION_TTL)

    assert sessions.user_for(token) is not None

    row = _row(test_engine, token)
    assert abs(row.expires_at - (datetime.now(UTC) + SESSION_TTL)) < timedelta(minutes=1)
    assert abs(row.last_used_at - datetime.now(UTC)) < timedelta(minutes=1)


def test_does_not_slide_within_a_day(sessions, user, test_engine):
    token = sessions.create(user.id)
    hour_ago = datetime.now(UTC) - timedelta(hours=1)
    expires_at = hour_ago + SESSION_TTL
    _set(test_engine, token, last_used_at=hour_ago, expires_at=expires_at)

    assert sessions.user_for(token) is not None

    row = _row(test_engine, token)
    assert row.expires_at == expires_at
    assert row.last_used_at == hour_ago


def test_delete_removes_session_and_is_idempotent(sessions, user):
    token = sessions.create(user.id)

    sessions.delete(token)
    sessions.delete(token)

    assert sessions.user_for(token) is None


def test_delete_only_removes_that_session(sessions, user):
    kept, removed = sessions.create(user.id), sessions.create(user.id)

    sessions.delete(removed)

    assert sessions.user_for(kept) is not None
