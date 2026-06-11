"""Confirm yfinance Ticker.history() can provide daily & weekly price changes.

Run: source venv/bin/activate && PYTHONPATH=. python scripts/test_yfinance_price_change.py
"""

import yfinance as yf

TICKERS = ["AAPL", "MSFT", "NVDA", "VWCE.DE"]


def daily_change(ticker: str) -> dict:
    """Daily change = last close vs previous close (period=5d covers weekends/holidays)."""
    hist = yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=True)
    if len(hist) < 2:
        raise ValueError(f"{ticker}: not enough rows ({len(hist)})")
    prev_close = hist["Close"].iloc[-2]
    last_close = hist["Close"].iloc[-1]
    return {
        "ticker": ticker,
        "prev_date": hist.index[-2].date().isoformat(),
        "last_date": hist.index[-1].date().isoformat(),
        "prev_close": round(float(prev_close), 2),
        "last_close": round(float(last_close), 2),
        "change": round(float(last_close - prev_close), 2),
        "change_pct": round(float((last_close - prev_close) / prev_close * 100), 2),
    }


def weekly_change(ticker: str) -> dict:
    """Weekly change via interval=1wk — compares last two weekly bars."""
    hist = yf.Ticker(ticker).history(period="1mo", interval="1wk", auto_adjust=True)
    if len(hist) < 2:
        raise ValueError(f"{ticker}: not enough weekly rows ({len(hist)})")
    prev_close = hist["Close"].iloc[-2]
    last_close = hist["Close"].iloc[-1]
    return {
        "ticker": ticker,
        "prev_week": hist.index[-2].date().isoformat(),
        "last_week": hist.index[-1].date().isoformat(),
        "prev_close": round(float(prev_close), 2),
        "last_close": round(float(last_close), 2),
        "change_pct": round(float((last_close - prev_close) / prev_close * 100), 2),
    }


def batch_daily_change(tickers: list[str]) -> None:
    """Batch variant — single request for all tickers via yf.download."""
    data = yf.download(tickers, period="5d", interval="1d", auto_adjust=True, progress=False)
    closes = data["Close"]
    for t in tickers:
        series = closes[t].dropna()
        if len(series) < 2:
            print(f"  {t}: insufficient data")
            continue
        prev, last = series.iloc[-2], series.iloc[-1]
        pct = (last - prev) / prev * 100
        print(f"  {t}: {prev:.2f} -> {last:.2f} ({pct:+.2f}%)  [{series.index[-1].date()}]")


if __name__ == "__main__":
    print("=== Daily change (per-ticker Ticker.history) ===")
    for t in TICKERS:
        print(f"  {daily_change(t)}")

    print("\n=== Weekly change (interval=1wk) ===")
    for t in TICKERS:
        print(f"  {weekly_change(t)}")

    print("\n=== Batch daily change (yf.download, 1 request) ===")
    batch_daily_change(TICKERS)
