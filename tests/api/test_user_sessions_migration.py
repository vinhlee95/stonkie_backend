import uuid

import pytest
from sqlalchemy import text

from alembic import command
from alembic.config import Config
from tests.api.conftest import PROJECT_ROOT

SESSIONS_REVISION = "a1c3e5f7b9d2"
PREVIOUS_REVISION = "e7b2c4d6f8a1"


@pytest.fixture()
def alembic_cfg(test_engine):
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    yield cfg
    # Leave the shared test DB empty and at head for the rest of the session.
    with test_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE users CASCADE"))
    command.upgrade(cfg, "head")


def _insert_user(connection) -> str:
    user_id = str(uuid.uuid4())
    connection.execute(
        text("INSERT INTO users (id, google_sub, email) VALUES (CAST(:id AS uuid), :sub, :email)"),
        {"id": user_id, "sub": f"mig-{user_id}", "email": f"{user_id}@example.com"},
    )
    return user_id


def test_upgrade_creates_user_sessions(test_engine, alembic_cfg):
    command.downgrade(alembic_cfg, PREVIOUS_REVISION)
    command.upgrade(alembic_cfg, SESSIONS_REVISION)

    with test_engine.connect() as connection:
        columns = {
            row[0]
            for row in connection.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'user_sessions'")
            )
        }
    assert columns == {"id", "user_id", "token_hash", "created_at", "last_used_at", "expires_at"}


def test_deleting_user_cascades_sessions(test_engine, alembic_cfg):
    with test_engine.begin() as connection:
        user_id = _insert_user(connection)
        connection.execute(
            text(
                "INSERT INTO user_sessions (user_id, token_hash, expires_at) "
                "VALUES (CAST(:user_id AS uuid), :hash, now() + interval '90 days')"
            ),
            {"user_id": user_id, "hash": "a" * 64},
        )
        connection.execute(text("DELETE FROM users WHERE id = CAST(:id AS uuid)"), {"id": user_id})

    with test_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM user_sessions")).scalar_one() == 0


def test_token_hash_is_unique(test_engine, alembic_cfg):
    insert = text(
        "INSERT INTO user_sessions (user_id, token_hash, expires_at) "
        "VALUES (CAST(:user_id AS uuid), :hash, now() + interval '90 days')"
    )
    with test_engine.begin() as connection:
        user_id = _insert_user(connection)
        connection.execute(insert, {"user_id": user_id, "hash": "b" * 64})

    with pytest.raises(Exception, match="uq_user_sessions_token_hash"):
        with test_engine.begin() as connection:
            connection.execute(insert, {"user_id": user_id, "hash": "b" * 64})


def test_downgrade_drops_user_sessions(test_engine, alembic_cfg):
    command.downgrade(alembic_cfg, PREVIOUS_REVISION)

    with test_engine.connect() as connection:
        assert connection.execute(text("SELECT to_regclass('user_sessions')")).scalar_one() is None
