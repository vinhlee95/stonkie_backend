import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from connectors import user as user_connector_module
from connectors.user import UserConnector
from models.user import User


@pytest.fixture()
def connector(test_engine, db_session, monkeypatch):
    # db_session is requested for its teardown (truncates users between tests).
    monkeypatch.setattr(
        user_connector_module,
        "SessionLocal",
        sessionmaker(bind=test_engine, autocommit=False, autoflush=False),
    )
    return UserConnector()


def test_first_upsert_inserts_user(connector):
    user = connector.upsert(google_sub="g-1", email="a@example.com", name="Ann", avatar_url="https://img/a.png")

    assert len(user.id) == 36
    assert (user.google_sub, user.email, user.name, user.avatar_url) == (
        "g-1",
        "a@example.com",
        "Ann",
        "https://img/a.png",
    )
    assert user.created_at is not None
    assert user.last_seen_at is not None


def test_repeat_upsert_with_changed_profile_updates_profile_and_last_seen(connector, db_session):
    first = connector.upsert(google_sub="g-1", email="a@example.com", name="Ann", avatar_url=None)

    second = connector.upsert(google_sub="g-1", email="new@example.com", name="Ann B", avatar_url="https://img/b.png")

    assert second.id == first.id
    assert (second.email, second.name, second.avatar_url) == ("new@example.com", "Ann B", "https://img/b.png")
    assert second.created_at == first.created_at
    assert second.last_seen_at > first.last_seen_at
    assert db_session.scalar(select(func.count()).select_from(User)) == 1


def test_missing_name_and_avatar_stored_as_null(connector):
    user = connector.upsert(google_sub="g-2", email="b@example.com", name=None, avatar_url=None)

    assert user.name is None
    assert user.avatar_url is None


def test_repeat_upsert_with_same_profile_within_window_skips_write(connector):
    first = connector.upsert(google_sub="g-1", email="a@example.com", name="Ann", avatar_url=None)

    second = connector.upsert(google_sub="g-1", email="a@example.com", name="Ann", avatar_url=None)

    assert second.id == first.id
    assert second.last_seen_at == first.last_seen_at


def test_repeat_upsert_after_window_refreshes_last_seen(connector, test_engine):
    first = connector.upsert(google_sub="g-1", email="a@example.com", name="Ann", avatar_url=None)
    with test_engine.begin() as conn:
        conn.execute(text("UPDATE users SET last_seen_at = now() - interval '10 minutes'"))

    second = connector.upsert(google_sub="g-1", email="a@example.com", name="Ann", avatar_url=None)

    assert second.last_seen_at > first.last_seen_at
