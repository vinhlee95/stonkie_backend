import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from api.portfolio import get_yfinance_client
from connectors import cache
from connectors import portfolio as portfolio_connector_module
from connectors import user as user_connector_module
from main import app
from tests.api.test_me import SECRET, make_token
from tests.api.test_quotes_price_changes import NY_TZ, FakeRedis, FakeYFinanceClient, make_history

HISTORIES = {
    "AAPL": make_history([200.0, 210.0], tz=NY_TZ),
    "NOKIA.HE": make_history([5.0, 4.0], tz=NY_TZ),
    "VOD.L": make_history([7000.0, 7200.0], tz=NY_TZ),
    "USDEUR=X": make_history([0.9, 0.8], tz=NY_TZ),
    "GBPEUR=X": make_history([1.1, 1.2], tz=NY_TZ),
}
CURRENCIES = {"AAPL": "USD", "NOKIA.HE": "EUR", "VOD.L": "GBp"}


def auth(sub: str = "google-123") -> dict:
    return {"Authorization": f"Bearer {make_token(sub=sub, email=f'{sub}@example.com')}"}


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    monkeypatch.setattr("connectors.cache.redis_client", FakeRedis())


@pytest.fixture()
def client(test_engine, db_session, monkeypatch):
    monkeypatch.setenv("BACKEND_JWT_SECRET", SECRET)
    session_local = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(user_connector_module, "SessionLocal", session_local)
    monkeypatch.setattr(portfolio_connector_module, "SessionLocal", session_local)
    fake = FakeYFinanceClient(HISTORIES, currencies=CURRENCIES)
    app.dependency_overrides[get_yfinance_client] = lambda: fake
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_yfinance_client, None)


def test_requires_auth(client):
    assert client.get("/api/me/portfolio").status_code == 401
    assert client.put("/api/me/portfolio/holdings/AAPL", json={"shares": 1, "avg_cost": 1}).status_code == 401
    assert client.delete("/api/me/portfolio/holdings/AAPL").status_code == 401


def test_empty_portfolio(client):
    body = client.get("/api/me/portfolio", headers=auth()).json()

    assert body["base_currency"] == "EUR"
    assert body["holdings"] == []
    assert body["summary"]["holdings_count"] == 0
    assert body["summary"]["total_value"] == 0


def test_add_values_holdings_in_eur(client):
    assert (
        client.put(
            "/api/me/portfolio/holdings/aapl", json={"shares": 10, "avg_cost": 100, "name": "Apple"}, headers=auth()
        ).status_code
        == 200
    )
    assert (
        client.put(
            "/api/me/portfolio/holdings/NOKIA.HE", json={"shares": 100, "avg_cost": 2}, headers=auth()
        ).status_code
        == 200
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


def test_put_replaces_existing_position(client):
    client.put("/api/me/portfolio/holdings/AAPL", json={"shares": 10, "avg_cost": 100, "name": "Apple"}, headers=auth())
    client.put("/api/me/portfolio/holdings/AAPL", json={"shares": 3, "avg_cost": 150}, headers=auth())

    holdings = client.get("/api/me/portfolio", headers=auth()).json()["holdings"]

    assert len(holdings) == 1
    assert holdings[0]["shares"] == 3 and holdings[0]["avg_cost"] == 150
    assert holdings[0]["name"] == "Apple"  # omitted name keeps the stored one


def test_edit_existing_holding_while_yahoo_down(client):
    client.put("/api/me/portfolio/holdings/AAPL", json={"shares": 10, "avg_cost": 100}, headers=auth())
    app.dependency_overrides[get_yfinance_client] = lambda: FakeYFinanceClient({"AAPL": RuntimeError("down")})

    response = client.put("/api/me/portfolio/holdings/AAPL", json={"shares": 5, "avg_cost": 100}, headers=auth())

    assert response.status_code == 200
    assert response.json()["shares"] == 5


def test_holdings_limit(client, monkeypatch):
    monkeypatch.setattr("services.portfolio.MAX_HOLDINGS_PER_USER", 1)
    client.put("/api/me/portfolio/holdings/AAPL", json={"shares": 1, "avg_cost": 1}, headers=auth())

    over = client.put("/api/me/portfolio/holdings/NOKIA.HE", json={"shares": 1, "avg_cost": 1}, headers=auth())
    edit = client.put("/api/me/portfolio/holdings/AAPL", json={"shares": 2, "avg_cost": 1}, headers=auth())

    assert over.status_code == 409
    assert edit.status_code == 200


def test_minor_unit_quote_normalised_to_major_currency(client):
    # avg_cost entered in pence, like the price Yahoo shows for .L listings.
    client.put("/api/me/portfolio/holdings/VOD.L", json={"shares": 100, "avg_cost": 6000}, headers=auth())

    row = client.get("/api/me/portfolio", headers=auth()).json()["holdings"][0]

    assert row["currency"] == "GBP"
    assert row["price"] == pytest.approx(72.0)
    assert row["avg_cost"] == pytest.approx(60.0)
    assert row["value"] == pytest.approx(100 * 72 * 1.2)
    assert row["cost_basis"] == pytest.approx(100 * 60 * 1.2)
    assert row["day_change"] == pytest.approx(100 * 2 * 1.2)
    assert row["total_return_percent"] == pytest.approx(20)


def test_holding_without_fx_rate_is_excluded_from_totals(client):
    client.put("/api/me/portfolio/holdings/NOKIA.HE", json={"shares": 100, "avg_cost": 2}, headers=auth())
    client.put("/api/me/portfolio/holdings/VOD.L", json={"shares": 100, "avg_cost": 6000}, headers=auth())
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
    client.put("/api/me/portfolio/holdings/AAPL", json={"shares": 1, "avg_cost": 1}, headers=auth())
    cache.redis_client.store.clear()  # drop the quote PUT cached with a currency
    app.dependency_overrides[get_yfinance_client] = lambda: FakeYFinanceClient(HISTORIES, currencies={})

    row = client.get("/api/me/portfolio", headers=auth()).json()["holdings"][0]

    assert row["currency"] is None and row["value"] is None
    assert row["day_change_percent"] is not None


def test_unknown_ticker_rejected(client):
    fake = FakeYFinanceClient({"NOPE": make_history([], tz=NY_TZ)})
    app.dependency_overrides[get_yfinance_client] = lambda: fake

    response = client.put("/api/me/portfolio/holdings/NOPE", json={"shares": 1, "avg_cost": 1}, headers=auth())

    assert response.status_code == 422


@pytest.mark.parametrize("body", [{"shares": 0, "avg_cost": 1}, {"shares": 1, "avg_cost": -1}, {"shares": 1}])
def test_invalid_body_rejected(client, body):
    assert client.put("/api/me/portfolio/holdings/AAPL", json=body, headers=auth()).status_code == 422


def test_invalid_ticker_rejected(client):
    assert (
        client.put("/api/me/portfolio/holdings/$$$", json={"shares": 1, "avg_cost": 1}, headers=auth()).status_code
        == 422
    )


def test_delete(client):
    client.put("/api/me/portfolio/holdings/AAPL", json={"shares": 1, "avg_cost": 1}, headers=auth())

    assert client.delete("/api/me/portfolio/holdings/AAPL", headers=auth()).status_code == 204
    assert client.delete("/api/me/portfolio/holdings/AAPL", headers=auth()).status_code == 404
    assert client.get("/api/me/portfolio", headers=auth()).json()["holdings"] == []


def test_users_are_isolated(client):
    client.put("/api/me/portfolio/holdings/AAPL", json={"shares": 1, "avg_cost": 1}, headers=auth("alice"))

    assert client.get("/api/me/portfolio", headers=auth("bob")).json()["holdings"] == []
    assert client.delete("/api/me/portfolio/holdings/AAPL", headers=auth("bob")).status_code == 404


def test_unpriced_holding_is_listed_but_excluded_from_totals(client):
    client.put("/api/me/portfolio/holdings/AAPL", json={"shares": 1, "avg_cost": 1}, headers=auth())
    app.dependency_overrides[get_yfinance_client] = lambda: FakeYFinanceClient({"AAPL": RuntimeError("down")})

    body = client.get("/api/me/portfolio", headers=auth()).json()

    assert body["holdings"][0]["value"] is None
    assert body["summary"]["priced_count"] == 0
    assert body["summary"]["total_value"] == 0
