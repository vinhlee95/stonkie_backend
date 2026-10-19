import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from alembic import command
from alembic.config import Config
from api.chat_limit import get_chat_limit_service
from main import app
from services.chat_limit import ChatLimitService

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MARKET_RECAP_BASE_REVISION = "d4e5f6a7b8c9"
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/stonkie_test",
)


@pytest.fixture(autouse=True)
def unlimited_chat():
    """Endpoint tests must not share (or need) Redis rate-limit counters; test_chat_limit.py overrides."""
    app.dependency_overrides[get_chat_limit_service] = lambda: ChatLimitService(incr=lambda _key, _ttl: None)
    yield
    app.dependency_overrides.pop(get_chat_limit_service, None)


@pytest.fixture(scope="session")
def test_engine():
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    alembic_cfg = Config(str(PROJECT_ROOT / "alembic.ini"))

    setup_engine = create_engine(TEST_DATABASE_URL)
    with setup_engine.begin() as connection:
        # alembic head creates BOTH market_recap and ticker_recap; drop both so a
        # re-run's `upgrade head` doesn't hit DuplicateTable (see phase-7 learnings).
        connection.execute(text("DROP TABLE IF EXISTS market_recap"))
        connection.execute(text("DROP TABLE IF EXISTS ticker_recap"))
        connection.execute(text("DROP TABLE IF EXISTS portfolio_lots"))
        connection.execute(text("DROP TABLE IF EXISTS portfolio_holdings"))
        connection.execute(text("DROP TABLE IF EXISTS user_sessions"))
        connection.execute(text("DROP TABLE IF EXISTS users"))
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    setup_engine.dispose()

    command.stamp(alembic_cfg, MARKET_RECAP_BASE_REVISION)
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(TEST_DATABASE_URL)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(test_engine):
    session_local = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)
    session = session_local()
    try:
        yield session
    finally:
        session.close()
        with test_engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE market_recap RESTART IDENTITY"))
            connection.execute(text("TRUNCATE TABLE ticker_recap RESTART IDENTITY"))
            connection.execute(text("TRUNCATE TABLE users CASCADE"))
