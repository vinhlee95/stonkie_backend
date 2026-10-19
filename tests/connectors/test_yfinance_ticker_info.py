from connectors import yfinance_client
from connectors.yfinance_client import TickerInfoDto, YFinanceClient


class FakeTicker:
    info: dict | Exception = {}

    def __init__(self, ticker):
        self.ticker = ticker

    def __getattribute__(self, name):
        value = object.__getattribute__(self, name)
        if name == "info" and isinstance(value, Exception):
            raise value
        return value


def use_info(monkeypatch, info):
    monkeypatch.setattr(FakeTicker, "info", info)
    monkeypatch.setattr(yfinance_client.yf, "Ticker", FakeTicker)


def test_ticker_info_prefers_long_name(monkeypatch):
    use_info(monkeypatch, {"quoteType": "EQUITY", "longName": "Nokia Oyj", "shortName": "Nokia"})
    assert YFinanceClient().get_ticker_info("NOKIA.HE") == TickerInfoDto(name="Nokia Oyj", quote_type="EQUITY")


def test_ticker_info_falls_back_to_short_name(monkeypatch):
    use_info(monkeypatch, {"quoteType": "ETF", "shortName": "Vanguard"})
    assert YFinanceClient().get_ticker_info("VOO") == TickerInfoDto(name="Vanguard", quote_type="ETF")


def test_ticker_info_error_is_none(monkeypatch):
    use_info(monkeypatch, RuntimeError("429"))
    assert YFinanceClient().get_ticker_info("X") is None


def test_ticker_info_without_quote_type_is_none(monkeypatch):
    use_info(monkeypatch, {})
    assert YFinanceClient().get_ticker_info("X") is None
