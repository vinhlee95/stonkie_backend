from __future__ import annotations

from urllib.parse import urlparse

from services.analyze_retrieval.source_policy import registrable_domain

_PUBLISHER_LABELS: dict[str, str] = {
    "reuters.com": "Reuters",
    "bloomberg.com": "Bloomberg",
    "ft.com": "Financial Times",
    "wsj.com": "Wall Street Journal",
    "cnbc.com": "CNBC",
    "apnews.com": "Associated Press",
    "nytimes.com": "The New York Times",
    "axios.com": "Axios",
    "barrons.com": "Barron's",
    "economist.com": "The Economist",
    "marketwatch.com": "MarketWatch",
    "sec.gov": "U.S. SEC",
    "companieshouse.gov.uk": "Companies House",
    "sedarplus.ca": "SEDAR+",
    "europa.eu": "European Union",
    "investing.com": "Investing.com",
    "finance.yahoo.com": "Yahoo Finance",
    "morningstar.com": "Morningstar",
    "stockanalysis.com": "StockAnalysis",
    "macrotrends.net": "Macrotrends",
    "simplywall.st": "Simply Wall St",
    "seekingalpha.com": "Seeking Alpha",
    "ishares.com": "iShares",
    "vanguard.com": "Vanguard",
    "ssga.com": "State Street Global Advisors",
    "invesco.com": "Invesco",
    "blackrock.com": "BlackRock",
    "schwab.com": "Charles Schwab",
    "fidelity.com": "Fidelity",
    "am.jpmorgan.com": "J.P. Morgan Asset Management",
    "statestreet.com": "State Street",
    "nasdaq.com": "Nasdaq",
    "investopedia.com": "Investopedia",
    "etf.com": "ETF.com",
    "tradingeconomics.com": "Trading Economics",
    "inderes.fi": "Inderes",
    "kauppalehti.fi": "Kauppalehti",
    "hs.fi": "Helsingin Sanomat",
    "yle.fi": "Yle",
    "arvopaperi.fi": "Arvopaperi",
    "hsx.vn": "HOSE",
    "hnx.vn": "HNX",
    "ssc.gov.vn": "Vietnam SSC",
    "sbv.gov.vn": "State Bank of Vietnam",
    "cafef.vn": "CafeF",
    "vneconomy.vn": "VnEconomy",
    "vietstock.vn": "Vietstock",
    "vir.com.vn": "Vietnam Investment Review",
    "baodautu.vn": "Bao Dau Tu",
    "nhandan.vn": "Nhan Dan",
    "tinnhanhchungkhoan.vn": "Tin Nhanh Chung Khoan",
    "vietnamplus.vn": "VietnamPlus",
    "dnse.com.vn": "DNSE",
    "bnews.vn": "Bnews",
    "ssi.com.vn": "SSI",
    "vndirect.com.vn": "VNDirect",
    "vcsc.com.vn": "VCSC",
    "hsc.com.vn": "HSC",
    "bsc.com.vn": "BSC",
    "mbs.com.vn": "MBS",
    "fpts.com.vn": "FPTS",
}


def publisher_label_for(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().lstrip("www.")
    if not host:
        return ""
    if host in _PUBLISHER_LABELS:
        return _PUBLISHER_LABELS[host]
    domain = registrable_domain(url)
    if domain and domain in _PUBLISHER_LABELS:
        return _PUBLISHER_LABELS[domain]
    if not domain:
        return ""
    base = domain.rsplit(".", 1)[0]
    return base.replace("-", " ").title()
