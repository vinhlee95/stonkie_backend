import pytest

from api.chat_limit import get_chat_limit_service
from services.chat_limit import ChatLimitService


@pytest.fixture(autouse=True)
def unlimited_chat():
    """Endpoint tests must not share (or need) Redis rate-limit counters; tests/api/test_chat_limit.py overrides."""
    from main import app

    app.dependency_overrides[get_chat_limit_service] = lambda: ChatLimitService(incr=lambda _key, _ttl: None)
    yield
    app.dependency_overrides.pop(get_chat_limit_service, None)
