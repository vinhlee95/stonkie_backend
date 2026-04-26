from datetime import date

from services.market_recap.schemas import PlannedQuery

HIGH_SIGNAL_SITES = [
    "reuters.com",
    "apnews.com",
    "cnbc.com",
    "marketwatch.com",
    "sec.gov",
    "federalreserve.gov",
]

VN_HIGH_SIGNAL_SITES = [
    "cafef.vn",
    "vietstock.vn",
    "vneconomy.vn",
    "vnexpress.net",
    "tinnhanhchungkhoan.vn",
    "vietnamfinance.vn",
]

FI_HIGH_SIGNAL_SITES = [
    "nasdaqomxnordic.com",
    "global.morningstar.com",
    "tradingeconomics.com",
    "investing.com",
    "globenewswire.com",
    "inderes.fi",
    "marketscreener.com",
    "reuters.com",
    "bloomberg.com",
]

_VN_TEMPLATES = {
    "weekly": "thị trường chứng khoán Việt Nam tuần qua",
    "daily": "thị trường chứng khoán Việt Nam phiên hôm nay",
}


def plan_queries(
    period_start: date, period_end: date, market: str = "US", cadence: str = "weekly"
) -> list[PlannedQuery]:
    market_key = market.upper()
    if market_key == "VN":
        vn_query = _VN_TEMPLATES.get(cadence.lower(), _VN_TEMPLATES["weekly"])
        scoped_queries = [PlannedQuery(query=vn_query, include_domains=[domain]) for domain in VN_HIGH_SIGNAL_SITES]
        return [PlannedQuery(query=vn_query), *scoped_queries]
    if market_key == "FI":
        month = period_start.strftime("%b")
        if cadence.lower() == "daily":
            open_query = PlannedQuery(
                query=f"Helsinki stock exchange Finnish market recap {month} {period_start.day}, {period_start.year}"
            )
            scoped_query_text = (
                f"Finland stock market session recap Helsinki exchange OMX Helsinki {month} "
                f"{period_start.day} {period_start.year}"
            )
        else:
            open_query = PlannedQuery(
                query=f"Helsinki stock exchange Finnish market recap week of {month} {period_start.day}-{period_end.day}, {period_start.year}"
            )
            scoped_query_text = (
                f"Finland stock market week recap Helsinki exchange OMX Helsinki {month} "
                f"{period_start.day}-{period_end.day} {period_start.year}"
            )
        scoped_queries = [
            PlannedQuery(query=scoped_query_text, include_domains=[domain]) for domain in FI_HIGH_SIGNAL_SITES
        ]
        return [open_query, *scoped_queries]

    month = period_start.strftime("%b")
    if cadence.lower() == "daily":
        open_query = PlannedQuery(query=f"US stock market recap {month} {period_start.day}, {period_start.year}")
        scoped_query_text = f"US market session recap {month} {period_start.day} {period_start.year}"
    else:
        open_query = PlannedQuery(
            query=f"US stock market recap week of {month} {period_start.day}-{period_end.day}, {period_start.year}"
        )
        scoped_query_text = f"US market week recap {month} {period_start.day}-{period_end.day} {period_start.year}"

    sites = HIGH_SIGNAL_SITES
    scoped_queries = [PlannedQuery(query=scoped_query_text, include_domains=[domain]) for domain in sites]

    return [open_query, *scoped_queries]
