import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

from connectors.finnhub_client import FinnhubFilingsClient

FAKE_KEY = "sk-fake-finnhub-key-123"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", FAKE_KEY)
    return FinnhubFilingsClient()


def test_token_sent_in_header_not_params_or_logs(client, caplog):
    response = MagicMock()
    response.json.return_value = []
    with patch("connectors.finnhub_client.requests.get", return_value=response) as get:
        with caplog.at_level(logging.INFO, logger="connectors.finnhub_client"):
            client.fetch_10q_filings("AAPL", from_date="2025-01-01")

    kwargs = get.call_args.kwargs
    assert kwargs["headers"] == {"X-Finnhub-Token": FAKE_KEY}
    assert "token" not in kwargs["params"]
    assert FAKE_KEY not in caplog.text


def test_http_error_log_does_not_leak_token(client, caplog):
    def fake_get(url, params, headers, timeout):
        prepared = requests.Request("GET", url, params=params, headers=headers).prepare()
        resp = requests.Response()
        resp.status_code = 401
        resp.reason = "Unauthorized"
        resp.url = prepared.url
        return resp

    with patch("connectors.finnhub_client.requests.get", side_effect=fake_get):
        with caplog.at_level(logging.INFO, logger="connectors.finnhub_client"):
            with pytest.raises(requests.exceptions.HTTPError) as exc:
                client.fetch_10q_filings("AAPL")

    assert FAKE_KEY not in str(exc.value)
    assert FAKE_KEY not in caplog.text
