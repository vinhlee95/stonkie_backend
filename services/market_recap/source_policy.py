from urllib.parse import urlsplit

ALLOWLIST_US = {
    "apnews.com",
    "barrons.com",
    "bea.gov",
    "bls.gov",
    "bloomberg.com",
    "cnbc.com",
    "federalreserve.gov",
    "ft.com",
    "marketwatch.com",
    "nasdaq.com",
    "nyse.com",
    "nytimes.com",
    "reuters.com",
    "sec.gov",
    "treasury.gov",
    "wsj.com",
}

ALLOWLIST_BY_MARKET = {
    "US": ALLOWLIST_US,
    "VN": {
        "cafef.vn",
        "vietstock.vn",
        "vneconomy.vn",
        "vnexpress.net",
        "tinnhanhchungkhoan.vn",
        "vietnamfinance.vn",
        "simplize.vn",
        "fireant.vn",
        "stockbiz.vn",
        "investing.com",
        "reuters.com",
    },
}


def registrable_domain(url: str) -> str:
    hostname = (urlsplit(url).hostname or "").lower()
    if not hostname:
        return ""

    labels = hostname.split(".")
    if len(labels) < 2:
        return hostname

    return ".".join(labels[-2:])


def is_allowlisted(url: str, market: str = "US") -> bool:
    market_key = market.upper()
    allowlist = ALLOWLIST_BY_MARKET.get(market_key, ALLOWLIST_BY_MARKET["US"])
    return registrable_domain(url) in allowlist
