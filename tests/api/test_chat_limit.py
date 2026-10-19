import pytest
from fastapi.testclient import TestClient

from api.chat_limit import get_chat_limit_service
from main import app
from services.chat_limit import ChatLimitService
from services.chat_limit.limits import WINDOWS

MINUTE, DAY = WINDOWS

LIMITED_PATHS = [
    "/api/v2/companies/AAPL/analyze",
    "/api/recaps/1/analyze",
    "/api/companies/AAPL/analyze",
    "/api/companies/AAPL/files/analyze",
    "/api/companies/AAPL/reports/analyze",
]


def _override(incr):
    app.dependency_overrides[get_chat_limit_service] = lambda: ChatLimitService(incr=incr)


@pytest.mark.parametrize("path", LIMITED_PATHS)
def test_public_chat_over_minute_limit_returns_429_before_any_work(path):
    _override(lambda _key, _ttl: 10**9)
    with TestClient(app) as client:
        response = client.post(path, json={"question": "Why did it move?"})
    assert response.status_code == 429
    assert response.json() == {"detail": MINUTE.message}
    assert response.headers["retry-after"] == str(MINUTE.seconds)


def test_day_limit_returns_its_own_message_and_retry_after():
    _override(lambda _key, ttl: 1 if ttl == MINUTE.seconds else DAY.limit + 1)
    with TestClient(app) as client:
        response = client.post("/api/recaps/1/analyze", json={"question": "Why did it move?"})
    assert response.status_code == 429
    assert response.json() == {"detail": DAY.message}
    assert response.headers["retry-after"] == str(DAY.seconds)


def test_counter_store_down_lets_the_request_through():
    _override(lambda _key, _ttl: None)
    with TestClient(app) as client:
        response = client.post("/api/recaps/1/analyze", json={})
    assert response.status_code == 400  # reached the handler's own validation, not limited


def test_limit_keys_on_last_forwarded_hop_not_spoofable_headers():
    keys: list[str] = []

    def incr(key: str, _ttl: int) -> None:
        keys.append(key)
        return None

    _override(incr)
    with TestClient(app) as client:
        client.post(
            "/api/recaps/1/analyze",
            json={},
            headers={"X-Real-IP": "198.51.100.1", "X-Forwarded-For": "198.51.100.2, 203.0.113.7"},
        )
    assert keys and all(":203.0.113.7:" in key for key in keys)
