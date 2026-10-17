import time
from types import SimpleNamespace

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from api.auth import get_google_verifier
from api.portfolio import get_portfolio_service
from connectors import session as session_connector_module
from connectors import user as user_connector_module
from main import app
from services.auth import AuthError, TokenClaims

SECRET = "s" * 32
CLAIMS = TokenClaims(sub="google-123", email="a@example.com", name="Ann", picture="https://img/a.png")


def web_token() -> str:
    now = int(time.time())
    payload = {"sub": CLAIMS.sub, "email": CLAIMS.email, "iss": "stonkie-web", "aud": "stonkie-api"}
    return jwt.encode({**payload, "iat": now, "exp": now + 300}, SECRET, algorithm="HS256")


def fake_verifier(id_token: str) -> TokenClaims:
    if id_token != "good-google-token":
        raise AuthError("bad token")
    return CLAIMS


@pytest.fixture()
def client(test_engine, db_session, monkeypatch):
    # db_session is requested for its teardown (truncates users, cascading to sessions).
    monkeypatch.setenv("BACKEND_JWT_SECRET", SECRET)
    session_local = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(user_connector_module, "SessionLocal", session_local)
    monkeypatch.setattr(session_connector_module, "SessionLocal", session_local)
    app.dependency_overrides[get_google_verifier] = lambda: fake_verifier
    app.dependency_overrides[get_portfolio_service] = lambda: SimpleNamespace(
        get_portfolio=lambda user_id: {"user_id": user_id}
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def sign_in(client) -> str:
    response = client.post("/api/auth/google", json={"id_token": "good-google-token"})
    assert response.status_code == 200
    return response.json()["token"]


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_google_sign_in_returns_token_and_user(client):
    response = client.post("/api/auth/google", json={"id_token": "good-google-token"})

    assert response.status_code == 200
    body = response.json()
    assert len(body["token"]) >= 40
    assert body["user"]["email"] == "a@example.com"
    assert body["user"]["name"] == "Ann"
    assert body["user"]["avatar_url"] == "https://img/a.png"


def test_session_token_authorizes_me_and_portfolio(client):
    token = sign_in(client)

    me = client.get("/api/me", headers=bearer(token))
    portfolio = client.get("/api/me/portfolio", headers=bearer(token))

    assert me.status_code == 200
    assert portfolio.status_code == 200
    assert portfolio.json() == {"user_id": me.json()["id"]}


def test_same_google_account_as_web_is_same_user(client):
    token = sign_in(client)

    ios_id = client.get("/api/me", headers=bearer(token)).json()["id"]
    web_id = client.get("/api/me", headers=bearer(web_token())).json()["id"]

    assert ios_id == web_id


def test_invalid_google_token_401(client):
    response = client.post("/api/auth/google", json={"id_token": "forged"})

    assert response.status_code == 401


def test_missing_id_token_422(client):
    assert client.post("/api/auth/google", json={}).status_code == 422


def test_not_configured_503(client, monkeypatch):
    app.dependency_overrides.pop(get_google_verifier)
    monkeypatch.delenv("GOOGLE_IOS_CLIENT_ID", raising=False)

    response = client.post("/api/auth/google", json={"id_token": "anything"})

    assert response.status_code == 503


def test_logout_revokes(client):
    token = sign_in(client)

    assert client.post("/api/auth/logout", headers=bearer(token)).status_code == 204
    assert client.get("/api/me", headers=bearer(token)).status_code == 401


def test_logout_unknown_token_204(client):
    assert client.post("/api/auth/logout", headers=bearer("never-issued")).status_code == 204


def test_logout_without_header_204(client):
    assert client.post("/api/auth/logout").status_code == 204


def test_unknown_opaque_token_401(client):
    assert client.get("/api/me", headers=bearer("never-issued")).status_code == 401


def test_session_works_without_backend_jwt_secret(client, monkeypatch):
    token = sign_in(client)
    monkeypatch.delenv("BACKEND_JWT_SECRET")

    assert client.get("/api/me", headers=bearer(token)).status_code == 200
