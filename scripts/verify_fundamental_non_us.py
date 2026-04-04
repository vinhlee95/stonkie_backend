"""
Verify fundamental data fetching for non-US stocks (e.g. MANTA.HE).

Checks both Alpha Vantage and Finnhub for MANTA.HE (Helsinki exchange).

Usage:
    PYTHONPATH=. python scripts/verify_fundamental_non_us.py
"""

import json
import os

import requests
from dotenv import load_dotenv

load_dotenv()

ALPHA_VANTAGE_API_KEY = os.getenv("ALPHA_VANTAGE_API_KEY")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
TICKER = "MANTA.HE"


def section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(title)
    print("=" * 60)


# --- Alpha Vantage ---


def check_alpha_vantage(ticker: str) -> None:
    section(f"Alpha Vantage OVERVIEW — {ticker}")
    if not ALPHA_VANTAGE_API_KEY:
        print("SKIP — ALPHA_VANTAGE_API_KEY not set")
        return

    url = f"https://www.alphavantage.co/query" f"?function=OVERVIEW&symbol={ticker}&apikey={ALPHA_VANTAGE_API_KEY}"
    data = requests.get(url, timeout=15).json()

    name = data.get("Name")
    market_cap = data.get("MarketCapitalization")
    if not data:
        print("FAIL — empty response")
    elif not name or not market_cap:
        print("FAIL — missing Name or MarketCapitalization")
        print(f"  Keys:         {list(data.keys())}")
        print(f"  Raw response: {json.dumps(data, indent=2)[:400]}")
    else:
        print("OK")
        print(f"  Name:      {name}")
        print(f"  Exchange:  {data.get('Exchange')}")
        print(f"  Country:   {data.get('Country')}")
        print(f"  MarketCap: {market_cap}")


# --- Finnhub ---


def check_finnhub_profile(ticker: str) -> None:
    section(f"Finnhub /stock/profile2 — {ticker}")
    if not FINNHUB_API_KEY:
        print("SKIP — FINNHUB_API_KEY not set")
        return

    url = f"https://finnhub.io/api/v1/stock/profile2?symbol={ticker}&token={FINNHUB_API_KEY}"
    data = requests.get(url, timeout=15).json()

    name = data.get("name")
    market_cap = data.get("marketCapitalization")
    if not data:
        print("FAIL — empty response")
    elif not name or not market_cap:
        print("FAIL — missing name or marketCapitalization")
        print(f"  Keys:         {list(data.keys())}")
        print(f"  Raw response: {json.dumps(data, indent=2)[:400]}")
    else:
        print("OK")
        print(f"  Name:        {name}")
        print(f"  Exchange:    {data.get('exchange')}")
        print(f"  Country:     {data.get('country')}")
        print(f"  Currency:    {data.get('currency')}")
        print(f"  MarketCap:   {market_cap}M")
        print(f"  Industry:    {data.get('finnhubIndustry')}")
        print(f"  IPO date:    {data.get('ipo')}")
        print(f"  Shares out:  {data.get('shareOutstanding')}")


def check_finnhub_metrics(ticker: str) -> None:
    section(f"Finnhub /stock/metric — {ticker}")
    if not FINNHUB_API_KEY:
        print("SKIP — FINNHUB_API_KEY not set")
        return

    url = f"https://finnhub.io/api/v1/stock/metric?symbol={ticker}&metric=all&token={FINNHUB_API_KEY}"
    data = requests.get(url, timeout=15).json()

    metric = data.get("metric", {})
    if not metric:
        print("FAIL — empty metric response")
        print(f"  Raw response: {json.dumps(data, indent=2)[:400]}")
    else:
        print("OK")
        fields = [
            "peBasicExclExtraTTM",
            "epsBasicExclExtraAnnual",
            "dividendYieldIndicatedAnnual",
            "revenueGrowth3Y",
            "netProfitMarginAnnual",
            "52WeekHigh",
            "52WeekLow",
        ]
        for f in fields:
            print(f"  {f}: {metric.get(f)}")


def check_yfinance(ticker: str) -> None:
    section(f"yfinance .info — {ticker}")
    import yfinance as yf

    info = yf.Ticker(ticker).info
    name = info.get("longName") or info.get("shortName")
    market_cap = info.get("marketCap")

    if not name or not market_cap:
        print("FAIL — missing name or marketCap")
        print(f"  Keys:         {list(info.keys())[:20]}")
        print(f"  Raw sample:   {json.dumps({k: info[k] for k in list(info.keys())[:10]}, indent=2)}")
    else:
        print("OK")
        print(f"  Name:        {name}")
        print(f"  Exchange:    {info.get('exchange')}")
        print(f"  Country:     {info.get('country')}")
        print(f"  Currency:    {info.get('currency')}")
        print(f"  MarketCap:   {market_cap:,}")
        print(f"  P/E (TTM):   {info.get('trailingPE')}")
        print(f"  EPS (TTM):   {info.get('trailingEps')}")
        print(f"  Sector:      {info.get('sector')}")
        print(f"  Industry:    {info.get('industry')}")
        print(f"  Div yield:   {info.get('dividendYield')}")


if __name__ == "__main__":
    check_alpha_vantage(TICKER)
    check_finnhub_profile(TICKER)
    check_finnhub_metrics(TICKER)
    check_yfinance(TICKER)

    print(f"\n{'=' * 60}")
    print("Done.")
