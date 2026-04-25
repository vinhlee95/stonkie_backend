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

HIGH_SIGNAL_SITES_BY_MARKET = {
    "US": HIGH_SIGNAL_SITES,
    "VN": [
        "fireant.vn",
        "vietstock.vn",
        "vneconomy.vn",
        "cafef.vn",
        "vnexpress.net",
        "reuters.com",
    ],
}


def plan_queries(period_start: date, period_end: date, market: str = "US") -> list[PlannedQuery]:
    market_key = market.upper()
    month = period_start.strftime("%b")
    if market_key == "VN":
        open_query = PlannedQuery(
            query=f"Vietnam stock market recap week of {month} {period_start.day}-{period_end.day}, {period_start.year}"
        )
        scoped_query_text = (
            f"Vietnam stock market week recap {month} {period_start.day}-{period_end.day} {period_start.year}"
        )
    else:
        open_query = PlannedQuery(
            query=f"US stock market recap week of {month} {period_start.day}-{period_end.day}, {period_start.year}"
        )
        scoped_query_text = f"US market week recap {month} {period_start.day}-{period_end.day} {period_start.year}"

    sites = HIGH_SIGNAL_SITES_BY_MARKET.get(market_key, HIGH_SIGNAL_SITES_BY_MARKET["US"])
    scoped_queries = [PlannedQuery(query=scoped_query_text, include_domains=[domain]) for domain in sites]

    return [open_query, *scoped_queries]
