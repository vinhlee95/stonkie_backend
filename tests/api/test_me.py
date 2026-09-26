import time

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from connectors import user as user_connector_module
from main import app

SECRET = "s" * 32


def make_token(**overrides) -> str:
    now = int(time.time())
    payload = {
        "sub": "google-123",
        "email": "a@example.com",
        "name": "Ann",
        "iss": "stonkie-web",
        "aud": "stonkie-api",
        "iat": now,
        "exp": now + 300,
    }
    payload.update(overrides)
    return jwt.encode(payload, SECRET, algorithm="HS256")


@pytest.fixture()
def client(test_engine, db_session, monkeypatch):
    # db_session is requested for its teardown (truncates users between tests).
    monkeypatch.setenv("BACKEND_JWT_SECRET", SECRET)
    monkeypatch.setattr(
        user_connector_module,
        "SessionLocal",
        sessionmaker(bind=test_engine, autocommit=False, autoflush=False),
    )
    with TestClient(app) as test_client:
        yield test_client


def test_me_returns_user_for_valid_token(client):
    response = client.get("/api/me", headers={"Authorization": f"Bearer {make_token()}"})

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "a@example.com"
    assert body["name"] == "Ann"
    assert body["avatar_url"] is None
    assert isinstance(body["id"], str) and len(body["id"]) == 36


def test_me_is_stable_across_calls(client):
    headers = {"Authorization": f"Bearer {make_token()}"}
    assert client.get("/api/me", headers=headers).json()["id"] == client.get("/api/me", headers=headers).json()["id"]


def test_lowercase_bearer_scheme_accepted(client):
    assert client.get("/api/me", headers={"Authorization": f"bearer {make_token()}"}).status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer not-a-jwt"},
    ],
)
def test_me_rejects_missing_or_invalid_token(client, headers):
    response = client.get("/api/me", headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}
    assert response.headers["www-authenticate"] == "Bearer"


def test_me_rejects_expired_token(client):
    now = int(time.time())
    token = make_token(iat=now - 400, exp=now - 100)
    assert client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_me_returns_503_when_secret_missing(client, monkeypatch):
    monkeypatch.delenv("BACKEND_JWT_SECRET")
    response = client.get("/api/me", headers={"Authorization": f"Bearer {make_token()}"})

    assert response.status_code == 503
    assert response.json() == {"detail": "Authentication not configured"}
