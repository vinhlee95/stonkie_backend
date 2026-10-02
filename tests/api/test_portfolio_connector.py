from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from connectors import portfolio as portfolio_connector_module
from connectors import user as user_connector_module
from connectors.portfolio import HoldingLimitExceeded, LotLimitExceeded, PortfolioConnector
from connectors.user import UserConnector


@pytest.fixture()
def users(test_engine, db_session, monkeypatch):
    """Alice and Bob's user ids; db_session truncates users (cascading to holdings and lots) afterwards."""
    session_local = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(user_connector_module, "SessionLocal", session_local)
    monkeypatch.setattr(portfolio_connector_module, "SessionLocal", session_local)
    connector = UserConnector()
    return [
        connector.upsert(google_sub=sub, email=f"{sub}@example.com", name=None, avatar_url=None).id
        for sub in ("alice", "bob")
    ]


@pytest.fixture()
def portfolio():
    return PortfolioConnector()


def add(portfolio, user_id, ticker="AAPL", shares=1.0, price=1.0, purchased_on=None, name=None, **caps):
    return portfolio.add_lot(
        user_id=user_id, ticker=ticker, name=name, shares=shares, price=price, purchased_on=purchased_on, **caps
    )


def test_lots_aggregate_into_one_holding(portfolio, users):
    alice, _ = users
    add(portfolio, alice, shares=10, price=100, purchased_on=date(2025, 1, 2), name="Apple")
    add(portfolio, alice, shares=30, price=200, purchased_on=date(2025, 6, 1))

    [holding] = portfolio.list_holdings(alice)

    assert (holding.ticker, holding.name) == ("AAPL", "Apple")
    assert holding.shares == 40
    assert holding.avg_cost == pytest.approx(175)
    assert [lot.purchased_on for lot in holding.lots] == [date(2025, 6, 1), date(2025, 1, 2)]
    assert all(lot.ticker == "AAPL" for lot in holding.lots)


def test_undated_lots_sort_last_newest_first(portfolio, users):
    alice, _ = users
    first_undated = add(portfolio, alice)
    dated = add(portfolio, alice, purchased_on=date(2024, 1, 1))
    second_undated = add(portfolio, alice)

    [holding] = portfolio.list_holdings(alice)

    assert [lot.id for lot in holding.lots] == [dated.id, second_undated.id, first_undated.id]


def test_holdings_listed_by_ticker(portfolio, users):
    alice, _ = users
    add(portfolio, alice, ticker="MSFT")
    add(portfolio, alice, ticker="AAPL")

    assert [h.ticker for h in portfolio.list_holdings(alice)] == ["AAPL", "MSFT"]


def test_omitted_name_keeps_stored_one(portfolio, users):
    alice, _ = users
    add(portfolio, alice, name="Apple")
    add(portfolio, alice)

    assert portfolio.list_holdings(alice)[0].name == "Apple"


def test_holding_cap_applies_to_new_tickers_only(portfolio, users):
    alice, _ = users
    add(portfolio, alice, ticker="AAPL", max_holdings=1)
    add(portfolio, alice, ticker="AAPL", max_holdings=1)  # another lot of a held ticker

    with pytest.raises(HoldingLimitExceeded):
        add(portfolio, alice, ticker="MSFT", max_holdings=1)


def test_lot_cap_is_per_holding(portfolio, users):
    alice, _ = users
    add(portfolio, alice, max_lots=2)
    add(portfolio, alice, max_lots=2)

    with pytest.raises(LotLimitExceeded):
        add(portfolio, alice, max_lots=2)
    add(portfolio, alice, ticker="MSFT", max_lots=2)
    assert len(portfolio.list_holdings(alice)[0].lots) == 2


def test_update_lot_changes_only_given_fields_and_can_clear_date(portfolio, users):
    alice, _ = users
    lot = add(portfolio, alice, shares=1, price=10, purchased_on=date(2025, 1, 1))

    updated = portfolio.update_lot(user_id=alice, lot_id=lot.id, changes={"shares": 2})
    assert (updated.shares, updated.price, updated.purchased_on) == (2, 10, date(2025, 1, 1))

    cleared = portfolio.update_lot(user_id=alice, lot_id=lot.id, changes={"purchased_on": None})
    assert cleared.purchased_on is None and cleared.shares == 2


def test_other_users_lot_cannot_be_updated_or_deleted(portfolio, users):
    alice, bob = users
    lot = add(portfolio, alice)

    assert portfolio.update_lot(user_id=bob, lot_id=lot.id, changes={"shares": 5}) is None
    assert portfolio.delete_lot(user_id=bob, lot_id=lot.id) is False
    assert portfolio.list_holdings(alice)[0].shares == 1
    assert portfolio.list_holdings(bob) == []


def test_deleting_last_lot_removes_the_holding(portfolio, users):
    alice, _ = users
    first = add(portfolio, alice)
    second = add(portfolio, alice, shares=2)

    assert portfolio.delete_lot(user_id=alice, lot_id=first.id) is True
    assert portfolio.list_holdings(alice)[0].shares == 2
    assert portfolio.delete_lot(user_id=alice, lot_id=second.id) is True
    assert portfolio.list_holdings(alice) == []
    assert portfolio.delete_holding(user_id=alice, ticker="AAPL") is False  # holding row is gone too
    assert portfolio.delete_lot(user_id=alice, lot_id=second.id) is False


def test_delete_holding_cascades_to_lots(portfolio, users):
    alice, _ = users
    lot = add(portfolio, alice)

    assert portfolio.delete_holding(user_id=alice, ticker="AAPL") is True
    assert portfolio.update_lot(user_id=alice, lot_id=lot.id, changes={"shares": 2}) is None


def test_holding_without_lots_is_hidden_and_revived_by_next_lot(portfolio, users, test_engine):
    # Pre-lots code running between migration and deploy writes holdings without lots.
    alice, _ = users
    with test_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO portfolio_holdings (user_id, ticker, shares, avg_cost) "
                "VALUES (CAST(:user_id AS uuid), 'AAPL', 1, 1)"
            ),
            {"user_id": str(alice)},
        )
    assert portfolio.list_holdings(alice) == []

    add(portfolio, alice, shares=2, price=5)

    [holding] = portfolio.list_holdings(alice)
    assert (holding.shares, holding.avg_cost) == (2, 5)


def _legacy_position(test_engine, user_id, ticker="AAPL"):
    with test_engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT shares, avg_cost FROM portfolio_holdings "
                "WHERE user_id = CAST(:user_id AS uuid) AND ticker = :ticker"
            ),
            {"user_id": str(user_id), "ticker": ticker},
        ).one_or_none()


def test_lot_writes_keep_legacy_position_columns_in_sync(portfolio, users, test_engine):
    # Pre-lots code (deploy overlap, rollback) reads only portfolio_holdings.shares/avg_cost.
    alice, _ = users
    first = add(portfolio, alice, shares=10, price=100)
    assert _legacy_position(test_engine, alice) == (10, 100)

    second = add(portfolio, alice, shares=30, price=200)
    assert _legacy_position(test_engine, alice) == (40, 175)

    portfolio.update_lot(user_id=alice, lot_id=second.id, changes={"shares": 10})
    assert _legacy_position(test_engine, alice) == (20, 150)

    portfolio.delete_lot(user_id=alice, lot_id=first.id)
    assert _legacy_position(test_engine, alice) == (10, 200)


def test_update_lot_and_delete_holding_take_the_user_lock(portfolio, users, monkeypatch):
    # Serialised with add/delete so the legacy mirror and cascades can't race.
    alice, _ = users
    lot = add(portfolio, alice)
    locked = []
    real_lock = portfolio_connector_module._lock_user

    def recording_lock(db, user_id):
        locked.append(user_id)
        real_lock(db, user_id)

    monkeypatch.setattr(portfolio_connector_module, "_lock_user", recording_lock)

    portfolio.update_lot(user_id=alice, lot_id=lot.id, changes={"shares": 2})
    portfolio.delete_holding(user_id=alice, ticker="AAPL")

    assert locked == [alice, alice]


def test_held_tickers_includes_lotless_holdings(portfolio, users, test_engine):
    alice, _ = users
    add(portfolio, alice, ticker="MSFT")
    with test_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO portfolio_holdings (user_id, ticker, shares, avg_cost) "
                "VALUES (CAST(:user_id AS uuid), 'AAPL', 1, 1)"
            ),
            {"user_id": str(alice)},
        )

    assert portfolio.held_tickers(alice) == {"AAPL", "MSFT"}
