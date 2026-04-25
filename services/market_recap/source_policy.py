from urllib.parse import urlsplit

ALLOWLIST = {
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


def registrable_domain(url: str) -> str:
    hostname = (urlsplit(url).hostname or "").lower()
    if not hostname:
        return ""

    labels = hostname.split(".")
    if len(labels) < 2:
        return hostname

    return ".".join(labels[-2:])


def is_allowlisted(url: str) -> bool:
    return registrable_domain(url) in ALLOWLIST
