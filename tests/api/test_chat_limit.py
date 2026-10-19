import pytest
from fastapi.testclient import TestClient

from api.chat_limit import get_chat_limit_service
from main import app
from services.chat_limit import ChatLimitService
from services.chat_limit.limits import WINDOWS


@pytest.fixture
def exhausted():
    """Every counter already past its limit."""
    app.dependency_overrides[get_chat_limit_service] = lambda: ChatLimitService(incr=lambda _key, _ttl: 10**9)
    yield
    app.dependency_overrides.pop(get_chat_limit_service, None)


@pytest.mark.parametrize("path", ["/api/v2/companies/AAPL/analyze", "/api/recaps/1/analyze"])
def test_public_chat_over_limit_returns_429_before_any_work(exhausted, path):
    with TestClient(app) as client:
        response = client.post(path, json={"question": "Why did it move?"})
    assert response.status_code == 429
    assert response.json() == {"detail": WINDOWS[0].message}
    assert response.headers["retry-after"] == str(WINDOWS[0].seconds)


def test_limit_keys_on_real_ip_header():
    keys: list[str] = []

    def incr(key: str, _ttl: int) -> None:
        keys.append(key)
        return None

    app.dependency_overrides[get_chat_limit_service] = lambda: ChatLimitService(incr=incr)
    try:
        with TestClient(app) as client:
            client.post("/api/recaps/1/analyze", json={}, headers={"X-Real-IP": "203.0.113.7"})
    finally:
        app.dependency_overrides.pop(get_chat_limit_service, None)
    assert keys and all(":203.0.113.7:" in key for key in keys)
