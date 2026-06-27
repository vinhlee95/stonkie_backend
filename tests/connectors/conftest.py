import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from alembic import command
from alembic.config import Config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Parent of 0fa89db559ad (add ticker_recap table); stamp here then upgrade to
# create only the ticker_recap table for these tests.
TICKER_RECAP_BASE_REVISION = "a65904684216"
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/stonkie_test",
)


@pytest.fixture(scope="session")
def ticker_recap_engine():
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    alembic_cfg = Config(str(PROJECT_ROOT / "alembic.ini"))

    setup_engine = create_engine(TEST_DATABASE_URL)
    with setup_engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS ticker_recap"))
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    setup_engine.dispose()

    command.stamp(alembic_cfg, TICKER_RECAP_BASE_REVISION)
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(TEST_DATABASE_URL)
    yield engine
    engine.dispose()


@pytest.fixture()
def recap_connector(ticker_recap_engine, monkeypatch):
    """A TickerRecapConnector whose internal SessionLocal is bound to the test DB."""
    import connectors.ticker_recap as ticker_recap_module

    session_local = sessionmaker(bind=ticker_recap_engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(ticker_recap_module, "SessionLocal", session_local)
    try:
        yield ticker_recap_module.TickerRecapConnector()
    finally:
        with ticker_recap_engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE ticker_recap RESTART IDENTITY"))
