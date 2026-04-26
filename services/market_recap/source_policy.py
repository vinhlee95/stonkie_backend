from urllib.parse import urlsplit

_VN_MULTI_SUFFIXES = (".com.vn", ".gov.vn", ".org.vn", ".net.vn", ".edu.vn")

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
        "nhandan.vn",
        "vietnamplus.vn",
        "dnse.com.vn",
        "tienphong.vn",
        "hsx.vn",
        "hnx.vn",
        "ssc.gov.vn",
        "sbv.gov.vn",
        "baodautu.vn",
        "thoibaotaichinhvietnam.vn",
        "vir.com.vn",
        "bnews.vn",
        "thanhnien.vn",
        "tuoitre.vn",
        "doanhnhansaigon.vn",
        "ssi.com.vn",
        "vndirect.com.vn",
        "mbs.com.vn",
        "hsc.com.vn",
    },
    "FI": {
        "nasdaqomxnordic.com",
        "morningstar.com",
        "tradingeconomics.com",
        "investing.com",
        "globenewswire.com",
        "inderes.fi",
        "marketscreener.com",
        "reuters.com",
        "bloomberg.com",
    },
}


def registrable_domain(url: str) -> str:
    hostname = (urlsplit(url).hostname or "").lower()
    if not hostname:
        return ""

    for suffix in _VN_MULTI_SUFFIXES:
        if hostname.endswith(suffix):
            prefix = hostname[: -len(suffix)].strip(".")
            if not prefix:
                return suffix.lstrip(".")
            return f"{prefix.split('.')[-1]}{suffix}"

    labels = hostname.split(".")
    if len(labels) < 2:
        return hostname

    return ".".join(labels[-2:])


def is_allowlisted(url: str, market: str = "US") -> bool:
    market_key = market.upper()
    allowlist = ALLOWLIST_BY_MARKET.get(market_key, ALLOWLIST_BY_MARKET["US"])
    return registrable_domain(url) in allowlist
