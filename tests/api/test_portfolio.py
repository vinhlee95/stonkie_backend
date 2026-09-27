import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from api.portfolio import get_yfinance_client
from connectors import portfolio as portfolio_connector_module
from connectors import user as user_connector_module
from main import app
from tests.api.test_me import SECRET, make_token
from tests.api.test_quotes_price_changes import NY_TZ, FakeRedis, FakeYFinanceClient, make_history

HISTORIES = {
    "AAPL": make_history([200.0, 210.0], tz=NY_TZ),
    "NOKIA.HE": make_history([5.0, 4.0], tz=NY_TZ),
    "USDEUR=X": make_history([0.9, 0.8], tz=NY_TZ),
}
CURRENCIES = {"AAPL": "USD", "NOKIA.HE": "EUR"}


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
    client.put("/api/me/portfolio/holdings/AAPL", json={"shares": 10, "avg_cost": 100}, headers=auth())
    client.put("/api/me/portfolio/holdings/AAPL", json={"shares": 3, "avg_cost": 150}, headers=auth())

    holdings = client.get("/api/me/portfolio", headers=auth()).json()["holdings"]

    assert len(holdings) == 1
    assert holdings[0]["shares"] == 3 and holdings[0]["avg_cost"] == 150


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
