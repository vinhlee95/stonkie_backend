import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from api.portfolio import get_yfinance_client
from connectors import cache
from connectors import company as company_connector_module
from connectors import portfolio as portfolio_connector_module
from connectors import user as user_connector_module
from connectors.yfinance_client import LiveQuoteDto
from main import app
from services import price_history
from tests.api.test_me import SECRET, make_token
from tests.api.test_quotes_price_changes import NY_TZ, FakeRedis, FakeYFinanceClient, make_history

HISTORIES = {
    "AAPL": make_history([200.0, 210.0], tz=NY_TZ),
    "NOKIA.HE": make_history([5.0, 4.0], tz=NY_TZ),
    "VOD.L": make_history([7000.0, 7200.0], tz=NY_TZ),
    "BP.L": make_history([400.0, 410.0], tz=NY_TZ),
    "USDEUR=X": make_history([0.9, 0.8], tz=NY_TZ),
    "GBPEUR=X": make_history([1.1, 1.2], tz=NY_TZ),
}
INFOS = {
    "AAPL": {"quoteType": "EQUITY", "sector": "Technology", "country": "United States"},
    "NOKIA.HE": {"quoteType": "EQUITY", "sector": "Technology", "country": "Finland"},
}
CURRENCIES = {"AAPL": "USD", "NOKIA.HE": "EUR", "VOD.L": "GBp", "BP.L": "GBp"}


def auth(sub: str = "google-123") -> dict:
    return {"Authorization": f"Bearer {make_token(sub=sub, email=f'{sub}@example.com')}"}


def post_lot(client, ticker: str, shares: float = 1, price: float = 1, headers: dict | None = None, **extra):
    return client.post(
        f"/api/me/portfolio/holdings/{ticker}/lots",
        json={"shares": shares, "price": price, **extra},
        headers=headers or auth(),
    )


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    monkeypatch.setattr("connectors.cache.redis_client", FakeRedis())


@pytest.fixture()
def client(test_engine, db_session, monkeypatch):
    monkeypatch.setenv("BACKEND_JWT_SECRET", SECRET)
    session_local = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(user_connector_module, "SessionLocal", session_local)
    monkeypatch.setattr(portfolio_connector_module, "SessionLocal", session_local)
    monkeypatch.setattr(company_connector_module, "SessionLocal", session_local)
    fake = FakeYFinanceClient(HISTORIES, currencies=CURRENCIES, infos=INFOS)
    app.dependency_overrides[get_yfinance_client] = lambda: fake
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_yfinance_client, None)


def test_requires_auth(client):
    lot_url = f"/api/me/portfolio/lots/{uuid.uuid4()}"
    assert client.get("/api/me/portfolio").status_code == 401
    assert client.get("/api/me/portfolio/performance").status_code == 401
    assert client.post("/api/me/portfolio/holdings/AAPL/lots", json={"shares": 1, "price": 1}).status_code == 401
    assert client.patch(lot_url, json={"shares": 1}).status_code == 401
    assert client.delete(lot_url).status_code == 401
    assert client.delete("/api/me/portfolio/holdings/AAPL").status_code == 401


def test_empty_portfolio(client):
    body = client.get("/api/me/portfolio", headers=auth()).json()

    assert body["base_currency"] == "EUR"
    assert body["holdings"] == []
    assert body["summary"]["holdings_count"] == 0
    assert body["summary"]["total_value"] == 0


def test_add_values_holdings_in_eur(client):
    assert (
        client.post(
            "/api/me/portfolio/holdings/aapl/lots", json={"shares": 10, "price": 100, "name": "Apple"}, headers=auth()
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/me/portfolio/holdings/NOKIA.HE/lots", json={"shares": 100, "price": 2}, headers=auth()
        ).status_code
        == 201
    )

    body = client.get("/api/me/portfolio", headers=auth()).json()
    aapl, nokia = body["holdings"]  # sorted by value desc

    assert aapl["ticker"] == "AAPL" and aapl["name"] == "Apple"
    assert aapl["value"] == pytest.approx(10 * 210 * 0.8)
    assert aapl["cost_basis"] == pytest.approx(10 * 100 * 0.8)
    assert aapl["day_change"] == pytest.approx(10 * 10 * 0.8)
    assert aapl["total_return_percent"] == pytest.approx(110)
    assert nokia["value"] == pytest.approx(400)
    assert nokia["day_change"] == pytest.approx(-100)

    s = body["summary"]
    assert s["total_value"] == pytest.approx(1680 + 400)
    assert s["total_cost"] == pytest.approx(800 + 200)
    assert s["day_change"] == pytest.approx(80 - 100)
    assert s["day_change_percent"] == pytest.approx(-20 / (2080 + 20) * 100)
    assert aapl["weight"] + nokia["weight"] == pytest.approx(100)
    assert (aapl["sector"], aapl["country"], aapl["asset_type"]) == ("Technology", "United States", "Stock")
    assert (nokia["sector"], nokia["country"], nokia["asset_type"]) == ("Technology", "Finland", "Stock")


def test_new_ticker_while_yahoo_down_is_retryable(client):
    app.dependency_overrides[get_yfinance_client] = lambda: FakeYFinanceClient({"AAPL": RuntimeError("down")})

    response = client.post("/api/me/portfolio/holdings/AAPL/lots", json={"shares": 1, "price": 1}, headers=auth())

    assert response.status_code == 503


def test_holdings_limit(client, monkeypatch):
    monkeypatch.setattr("services.portfolio.MAX_HOLDINGS_PER_USER", 1)
    client.post("/api/me/portfolio/holdings/AAPL/lots", json={"shares": 1, "price": 1}, headers=auth())

    over = client.post("/api/me/portfolio/holdings/NOKIA.HE/lots", json={"shares": 1, "price": 1}, headers=auth())
    another_lot = client.post("/api/me/portfolio/holdings/AAPL/lots", json={"shares": 2, "price": 1}, headers=auth())

    assert over.status_code == 409
    assert another_lot.status_code == 201


def test_holdings_limit_enforced_on_insert(client, monkeypatch):
    # Simulates a concurrent POST: the service pre-check sees no holdings, so the connector must refuse.
    monkeypatch.setattr("services.portfolio.MAX_HOLDINGS_PER_USER", 1)
    client.post("/api/me/portfolio/holdings/AAPL/lots", json={"shares": 1, "price": 1}, headers=auth())
    monkeypatch.setattr(portfolio_connector_module.PortfolioConnector, "held_tickers", lambda self, user_id: set())

    response = client.post("/api/me/portfolio/holdings/NOKIA.HE/lots", json={"shares": 1, "price": 1}, headers=auth())

    assert response.status_code == 409


def test_minor_unit_quote_normalised_to_major_currency(client):
    # avg_cost entered in pence, like the price Yahoo shows for .L listings.
    client.post("/api/me/portfolio/holdings/VOD.L/lots", json={"shares": 100, "price": 6000}, headers=auth())

    row = client.get("/api/me/portfolio", headers=auth()).json()["holdings"][0]

    assert row["currency"] == "GBP"
    assert row["quote_currency"] == "GBp"  # unit of avg_cost
    assert row["price"] == pytest.approx(72.0)
    assert row["avg_cost"] == 6000  # returned in pence, as entered
    assert row["value"] == pytest.approx(100 * 72 * 1.2)
    assert row["cost_basis"] == pytest.approx(100 * 60 * 1.2)
    assert row["day_change"] == pytest.approx(100 * 2 * 1.2)
    assert row["total_return_percent"] == pytest.approx(20)


def test_holding_without_fx_rate_is_excluded_from_totals(client):
    client.post("/api/me/portfolio/holdings/NOKIA.HE/lots", json={"shares": 100, "price": 2}, headers=auth())
    client.post("/api/me/portfolio/holdings/VOD.L/lots", json={"shares": 100, "price": 6000}, headers=auth())
    histories = {**HISTORIES, "GBPEUR=X": RuntimeError("fx down")}
    app.dependency_overrides[get_yfinance_client] = lambda: FakeYFinanceClient(histories, currencies=CURRENCIES)

    body = client.get("/api/me/portfolio", headers=auth()).json()
    nokia, vod = body["holdings"]

    assert vod["ticker"] == "VOD.L"
    assert vod["currency"] == "GBP" and vod["price"] == pytest.approx(72.0)
    assert vod["value"] is None and vod["cost_basis"] is None and vod["day_change"] is None
    assert body["summary"]["priced_count"] == 1
    assert body["summary"]["total_value"] == pytest.approx(nokia["value"])


def test_holding_with_unknown_currency_is_not_valued(client):
    client.post("/api/me/portfolio/holdings/AAPL/lots", json={"shares": 1, "price": 1}, headers=auth())
    cache.redis_client.store.clear()  # drop the quote PUT cached with a currency
    app.dependency_overrides[get_yfinance_client] = lambda: FakeYFinanceClient(HISTORIES, currencies={})

    row = client.get("/api/me/portfolio", headers=auth()).json()["holdings"][0]

    assert row["currency"] is None and row["value"] is None
    assert row["day_change_percent"] is not None


def test_unknown_ticker_rejected(client):
    fake = FakeYFinanceClient({"NOPE": make_history([], tz=NY_TZ)})
    app.dependency_overrides[get_yfinance_client] = lambda: fake

    response = client.post("/api/me/portfolio/holdings/NOPE/lots", json={"shares": 1, "price": 1}, headers=auth())

    assert response.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"shares": 0, "price": 1},
        {"shares": 1, "price": -1},
        {"shares": 1},
        {"shares": 1e-7, "price": 1},  # would round to 0 in Numeric(20, 6)
        {"shares": 1, "price": 1e-7},
        {"shares": 1, "price": 1, "purchased_on": "not-a-date"},
    ],
)
def test_invalid_body_rejected(client, body):
    assert client.post("/api/me/portfolio/holdings/AAPL/lots", json=body, headers=auth()).status_code == 422


def test_invalid_ticker_rejected(client):
    assert (
        client.post("/api/me/portfolio/holdings/$$$/lots", json={"shares": 1, "price": 1}, headers=auth()).status_code
        == 422
    )


def test_delete(client):
    client.post("/api/me/portfolio/holdings/AAPL/lots", json={"shares": 1, "price": 1}, headers=auth())

    assert client.delete("/api/me/portfolio/holdings/AAPL", headers=auth()).status_code == 204
    assert client.delete("/api/me/portfolio/holdings/AAPL", headers=auth()).status_code == 404
    assert client.get("/api/me/portfolio", headers=auth()).json()["holdings"] == []


def test_users_are_isolated(client):
    client.post("/api/me/portfolio/holdings/AAPL/lots", json={"shares": 1, "price": 1}, headers=auth("alice"))

    assert client.get("/api/me/portfolio", headers=auth("bob")).json()["holdings"] == []
    assert client.delete("/api/me/portfolio/holdings/AAPL", headers=auth("bob")).status_code == 404


def test_unpriced_holding_is_listed_but_excluded_from_totals(client):
    client.post("/api/me/portfolio/holdings/AAPL/lots", json={"shares": 1, "price": 1}, headers=auth())
    cache.redis_client.store.clear()  # drop the quote PUT cached, so GET sees the outage
    app.dependency_overrides[get_yfinance_client] = lambda: FakeYFinanceClient({"AAPL": RuntimeError("down")})

    body = client.get("/api/me/portfolio", headers=auth()).json()

    assert body["holdings"][0]["price"] is None
    assert body["holdings"][0]["value"] is None
    assert body["summary"]["priced_count"] == 0
    assert body["summary"]["total_value"] == 0


def test_fx_rate_fetched_once_per_currency(client, monkeypatch):
    client.post("/api/me/portfolio/holdings/VOD.L/lots", json={"shares": 1, "price": 1}, headers=auth())
    client.post("/api/me/portfolio/holdings/BP.L/lots", json={"shares": 1, "price": 1}, headers=auth())
    monkeypatch.setattr("connectors.fx.cache.get_json", lambda key: None)  # force FX cache misses
    fake = FakeYFinanceClient(HISTORIES, currencies=CURRENCIES)
    app.dependency_overrides[get_yfinance_client] = lambda: fake

    body = client.get("/api/me/portfolio", headers=auth()).json()

    assert body["summary"]["priced_count"] == 2
    assert fake.calls.count("GBPEUR=X") == 1


def live(price: float, prev_close: float, currency: str | None, minute: int = 30) -> LiveQuoteDto:
    return LiveQuoteDto(
        price=price,
        prev_close=prev_close,
        currency=currency,
        market_time=datetime(2026, 9, 25, 18, minute, tzinfo=UTC),
        trading_date=date(2026, 9, 25),
    )


def test_live_quotes_value_holdings_and_missing_live_falls_back_to_daily_close(client):
    client.post("/api/me/portfolio/holdings/AAPL/lots", json={"shares": 10, "price": 100}, headers=auth())
    client.post("/api/me/portfolio/holdings/NOKIA.HE/lots", json={"shares": 100, "price": 2}, headers=auth())
    live_quotes = {"AAPL": live(220.0, 210.0, "USD"), "USDEUR=X": live(0.9, 0.9, "EUR")}
    fake = FakeYFinanceClient(HISTORIES, currencies=CURRENCIES, live_quotes=live_quotes)
    app.dependency_overrides[get_yfinance_client] = lambda: fake

    body = client.get("/api/me/portfolio", headers=auth()).json()
    aapl, nokia = body["holdings"]

    assert aapl["delayed"] is False
    assert aapl["price"] == pytest.approx(220)
    assert aapl["fx_rate"] == pytest.approx(0.9)
    assert aapl["value"] == pytest.approx(10 * 220 * 0.9)
    assert aapl["day_change"] == pytest.approx(10 * 10 * 0.9)
    assert aapl["day_change_percent"] == pytest.approx(4.76)
    assert aapl["trading_date"] == "2026-09-25"
    assert aapl["as_of"] == "2026-09-25T18:30:00+00:00"
    assert nokia["delayed"] is True
    assert nokia["as_of"] is None
    assert nokia["price"] == pytest.approx(4.0)  # last daily close
    s = body["summary"]
    assert s["as_of"] == "2026-09-25T18:30:00+00:00"
    assert s["delayed_count"] == 1


def test_summary_as_of_is_newest_live_quote(client):
    client.post("/api/me/portfolio/holdings/AAPL/lots", json={"shares": 1, "price": 1}, headers=auth())
    client.post("/api/me/portfolio/holdings/NOKIA.HE/lots", json={"shares": 1, "price": 1}, headers=auth())
    live_quotes = {"AAPL": live(220.0, 210.0, "USD", minute=45), "NOKIA.HE": live(4.5, 4.0, "EUR", minute=5)}
    fake = FakeYFinanceClient(HISTORIES, currencies=CURRENCIES, live_quotes=live_quotes)
    app.dependency_overrides[get_yfinance_client] = lambda: fake

    s = client.get("/api/me/portfolio", headers=auth()).json()["summary"]

    assert s["as_of"] == "2026-09-25T18:45:00+00:00"
    assert s["delayed_count"] == 0


def test_live_minor_unit_quote_normalised(client):
    client.post("/api/me/portfolio/holdings/VOD.L/lots", json={"shares": 100, "price": 6000}, headers=auth())
    live_quotes = {"VOD.L": live(7300.0, 7200.0, "GBp"), "GBPEUR=X": live(1.15, 1.15, "EUR")}
    fake = FakeYFinanceClient(HISTORIES, currencies=CURRENCIES, live_quotes=live_quotes)
    app.dependency_overrides[get_yfinance_client] = lambda: fake

    row = client.get("/api/me/portfolio", headers=auth()).json()["holdings"][0]

    assert row["delayed"] is False
    assert row["price"] == pytest.approx(73.0)
    assert row["value"] == pytest.approx(100 * 73 * 1.15)
    assert row["day_change"] == pytest.approx(100 * 1 * 1.15)


def test_all_delayed_portfolio_has_no_as_of(client):
    client.post("/api/me/portfolio/holdings/AAPL/lots", json={"shares": 1, "price": 1}, headers=auth())

    body = client.get("/api/me/portfolio", headers=auth()).json()

    assert body["holdings"][0]["delayed"] is True
    assert body["summary"]["as_of"] is None
    assert body["summary"]["delayed_count"] == 1


def test_live_row_without_fx_still_counts_for_as_of(client):
    client.post("/api/me/portfolio/holdings/VOD.L/lots", json={"shares": 1, "price": 1}, headers=auth())
    histories = {**HISTORIES, "GBPEUR=X": RuntimeError("fx down")}
    live_quotes = {"VOD.L": live(7300.0, 7200.0, "GBp"), "GBPEUR=X": RuntimeError("fx down")}
    app.dependency_overrides[get_yfinance_client] = lambda: FakeYFinanceClient(
        histories, currencies=CURRENCIES, live_quotes=live_quotes
    )

    body = client.get("/api/me/portfolio", headers=auth()).json()

    assert body["holdings"][0]["value"] is None
    assert body["summary"]["priced_count"] == 0
    assert body["summary"]["as_of"] == "2026-09-25T18:30:00+00:00"


def test_delayed_row_without_fx_still_counted(client):
    client.post("/api/me/portfolio/holdings/VOD.L/lots", json={"shares": 1, "price": 1}, headers=auth())
    histories = {**HISTORIES, "GBPEUR=X": RuntimeError("fx down")}
    app.dependency_overrides[get_yfinance_client] = lambda: FakeYFinanceClient(histories, currencies=CURRENCIES)

    body = client.get("/api/me/portfolio", headers=auth()).json()

    assert body["holdings"][0]["delayed"] is True and body["holdings"][0]["value"] is None
    assert body["summary"]["delayed_count"] == 1


LOTS_URL = "/api/me/portfolio/lots"


def portfolio_rows(client, headers: dict | None = None) -> list[dict]:
    return client.get("/api/me/portfolio", headers=headers or auth()).json()["holdings"]


def test_lots_of_same_ticker_aggregate_into_one_position(client):
    first = post_lot(client, "AAPL", shares=10, price=100, name="Apple", purchased_on="2025-01-02")
    second = post_lot(client, "aapl", shares=30, price=200, purchased_on="2025-06-01")

    [row] = portfolio_rows(client)

    assert first.status_code == 201 and second.status_code == 201
    assert first.json() == {
        "id": first.json()["id"],
        "ticker": "AAPL",
        "shares": 10,
        "price": 100,
        "purchased_on": "2025-01-02",
    }
    assert row["name"] == "Apple"
    assert row["shares"] == 40
    assert row["avg_cost"] == pytest.approx(175)
    assert row["cost_basis"] == pytest.approx(40 * 175 * 0.8)
    assert row["value"] == pytest.approx(40 * 210 * 0.8)
    assert [lot["purchased_on"] for lot in row["lots"]] == ["2025-06-01", "2025-01-02"]
    assert row["lots"][1] == {"id": first.json()["id"], "shares": 10, "price": 100, "purchased_on": "2025-01-02"}


def test_minor_unit_lots_are_valued_in_major_currency(client):
    post_lot(client, "VOD.L", shares=50, price=5000)
    post_lot(client, "VOD.L", shares=50, price=7000)

    [row] = portfolio_rows(client)

    assert row["avg_cost"] == pytest.approx(6000)  # pence, like the lots
    assert row["cost_basis"] == pytest.approx(100 * 60 * 1.2)
    assert row["value"] == pytest.approx(100 * 72 * 1.2)


def test_add_lot_to_held_ticker_while_yahoo_down(client):
    post_lot(client, "AAPL", shares=10, price=100)
    app.dependency_overrides[get_yfinance_client] = lambda: FakeYFinanceClient({"AAPL": RuntimeError("down")})

    response = post_lot(client, "AAPL", shares=5, price=100)

    assert response.status_code == 201


def test_lot_limit(client, monkeypatch):
    monkeypatch.setattr("services.portfolio.MAX_LOTS_PER_HOLDING", 1)
    post_lot(client, "AAPL")

    assert post_lot(client, "AAPL").status_code == 409
    assert post_lot(client, "NOKIA.HE").status_code == 201


def test_purchase_date_window(client):
    today = datetime.now(UTC).date()

    # A user east of UTC can already be on tomorrow's date.
    assert post_lot(client, "AAPL", purchased_on=(today + timedelta(days=1)).isoformat()).status_code == 201
    assert post_lot(client, "AAPL", purchased_on=(today + timedelta(days=2)).isoformat()).status_code == 422
    assert post_lot(client, "AAPL", purchased_on="1900-01-01").status_code == 201
    assert post_lot(client, "AAPL", purchased_on="1899-12-31").status_code == 422


def test_patch_lot(client):
    lot = post_lot(client, "AAPL", shares=1, price=10, purchased_on="2025-01-01").json()

    response = client.patch(f"{LOTS_URL}/{lot['id']}", json={"shares": 3}, headers=auth())
    cleared = client.patch(f"{LOTS_URL}/{lot['id']}", json={"purchased_on": None}, headers=auth())

    assert response.status_code == 200
    assert response.json() == {**lot, "shares": 3}
    assert cleared.json() == {**lot, "shares": 3, "purchased_on": None}
    assert portfolio_rows(client)[0]["shares"] == 3


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"shares": None},
        {"price": None},
        {"shares": 0},
        {"price": 1e-7},
        {"purchased_on": "1899-12-31"},
        {"ticker": "MSFT"},
    ],
)
def test_patch_lot_rejects_invalid_body(client, body):
    lot = post_lot(client, "AAPL").json()

    assert client.patch(f"{LOTS_URL}/{lot['id']}", json=body, headers=auth()).status_code == 422


def test_lots_are_private(client):
    lot = post_lot(client, "AAPL", headers=auth("alice")).json()

    assert client.patch(f"{LOTS_URL}/{lot['id']}", json={"shares": 2}, headers=auth("bob")).status_code == 404
    assert client.delete(f"{LOTS_URL}/{lot['id']}", headers=auth("bob")).status_code == 404
    assert portfolio_rows(client, auth("alice"))[0]["shares"] == 1


def test_delete_lot_and_last_lot_removes_position(client):
    first = post_lot(client, "AAPL", shares=1).json()
    second = post_lot(client, "AAPL", shares=2).json()

    assert client.delete(f"{LOTS_URL}/{first['id']}", headers=auth()).status_code == 204
    assert portfolio_rows(client)[0]["shares"] == 2
    assert client.delete(f"{LOTS_URL}/{second['id']}", headers=auth()).status_code == 204
    assert portfolio_rows(client) == []
    assert client.delete(f"{LOTS_URL}/{second['id']}", headers=auth()).status_code == 404


def test_delete_holding_removes_all_lots(client):
    lot = post_lot(client, "AAPL").json()
    post_lot(client, "AAPL")

    assert client.delete("/api/me/portfolio/holdings/AAPL", headers=auth()).status_code == 204
    assert client.patch(f"{LOTS_URL}/{lot['id']}", json={"shares": 2}, headers=auth()).status_code == 404


def test_malformed_lot_id_rejected(client):
    assert client.patch(f"{LOTS_URL}/not-a-uuid", json={"shares": 1}, headers=auth()).status_code == 422
    assert client.delete(f"{LOTS_URL}/not-a-uuid", headers=auth()).status_code == 422


def test_put_holding_is_gone(client):
    response = client.put("/api/me/portfolio/holdings/AAPL", json={"shares": 1, "avg_cost": 1}, headers=auth())

    assert response.status_code == 405


def test_lotless_legacy_holding_is_not_revalidated_on_first_lot(client, test_engine):
    # Pre-lots code running between migration and deploy leaves holdings without lots.
    post_lot(client, "AAPL")
    with test_engine.begin() as connection:
        connection.execute(text("DELETE FROM portfolio_lots"))
    cache.redis_client.store.clear()  # drop the quote the first POST cached, so a lookup would hit Yahoo
    app.dependency_overrides[get_yfinance_client] = lambda: FakeYFinanceClient({"AAPL": RuntimeError("down")})

    assert post_lot(client, "AAPL").status_code == 201


def test_purchase_date_error_names_the_allowed_window(client):
    response = post_lot(client, "AAPL", purchased_on="1899-12-31")

    assert response.status_code == 422
    assert "tomorrow (UTC)" in response.text


def test_performance_series_for_users_holdings(client, monkeypatch):
    monkeypatch.setattr(price_history, "_utcnow", lambda: datetime(2026, 10, 2, 12, tzinfo=UTC))
    post_lot(client, "AAPL", shares=2)
    post_lot(client, "NOKIA.HE", shares=10)
    post_lot(client, "AAPL", shares=99, headers=auth("someone-else"))
    days = pd.to_datetime(["2026-09-30", "2026-10-01"])
    fake = FakeYFinanceClient(
        HISTORIES,
        live_quotes={"AAPL": live(1.0, 1.0, "USD"), "NOKIA.HE": live(1.0, 1.0, "EUR")},
        close_histories={
            "AAPL": pd.Series([100.0, 110.0], index=days),
            "NOKIA.HE": pd.Series([4.0, 5.0], index=days),
            "^GSPC": pd.Series([5000.0, 5100.0], index=days),
            "USDEUR=X": pd.Series([0.9, 0.8], index=days),
        },
    )
    app.dependency_overrides[get_yfinance_client] = lambda: fake

    body = client.get("/api/me/portfolio/performance", headers=auth()).json()

    assert body == {
        "base_currency": "EUR",
        "points": [
            {"date": "2026-09-30", "portfolio_value": 220.0, "benchmark_value": 4500.0},
            {"date": "2026-10-01", "portfolio_value": 226.0, "benchmark_value": 4080.0},
        ],
        "excluded": [],
    }


def test_performance_empty_portfolio(client):
    body = client.get("/api/me/portfolio/performance", headers=auth()).json()

    assert body == {"base_currency": "EUR", "points": [], "excluded": []}


def _parse_stream(raw: bytes) -> list[dict]:
    return [json.loads(block) for block in raw.decode().strip().split("\n\n") if block.strip()]


class FakeChatService:
    calls: list[dict] = []

    def __init__(self, portfolio, yf_client):
        pass

    async def stream(self, **kwargs):
        FakeChatService.calls.append(kwargs)
        yield {"type": "conversation", "body": {"conversationId": "conv-1"}}
        yield {"type": "answer", "body": "Hi"}


@pytest.fixture()
def fake_chat(monkeypatch):
    FakeChatService.calls = []
    monkeypatch.setattr("api.portfolio.PortfolioChatStreamService", FakeChatService)
    return FakeChatService


def test_chat_requires_auth(client, fake_chat):
    assert client.post("/api/me/portfolio/chat", json={"question": "hi"}).status_code == 401


@pytest.mark.parametrize("body", [{}, {"question": ""}, {"question": "   "}, {"question": "x" * 2001}])
def test_chat_rejects_missing_question(client, fake_chat, body):
    assert client.post("/api/me/portfolio/chat", json=body, headers=auth()).status_code == 422


def test_chat_rejects_scope_outside_portfolio(client, fake_chat):
    post_lot(client, "AAPL")

    res = client.post("/api/me/portfolio/chat", json={"question": "hi", "scopeTicker": "NOKIA.HE"}, headers=auth())

    assert res.status_code == 422
    assert fake_chat.calls == []


def test_chat_streams_events_for_normalised_scope(client, fake_chat):
    post_lot(client, "AAPL")

    res = client.post(
        "/api/me/portfolio/chat",
        json={"question": "  Why is it up?  ", "scopeTicker": " aapl ", "conversationId": "conv-1"},
        headers=auth(),
    )

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert [e["type"] for e in _parse_stream(res.content)] == ["conversation", "answer"]
    call = fake_chat.calls[0]
    assert (call["question"], call["scope_ticker"], call["conversation_id"]) == ("Why is it up?", "AAPL", "conv-1")
    assert call["user_id"]


def test_chat_stream_failure_becomes_error_event(client, monkeypatch):
    class Boom(FakeChatService):
        async def stream(self, **kwargs):
            yield {"type": "conversation", "body": {"conversationId": "c"}}
            raise RuntimeError("boom")

    monkeypatch.setattr("api.portfolio.PortfolioChatStreamService", Boom)

    events = _parse_stream(client.post("/api/me/portfolio/chat", json={"question": "hi"}, headers=auth()).content)

    assert events[-1] == {"type": "error", "code": "internal", "body": "Something went wrong"}
