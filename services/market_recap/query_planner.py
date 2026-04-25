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


def plan_queries(period_start: date, period_end: date) -> list[PlannedQuery]:
    month = period_start.strftime("%b")
    open_query = PlannedQuery(
        query=f"US stock market recap week of {month} {period_start.day}-{period_end.day}, {period_start.year}"
    )
    scoped_query_text = f"US market week recap {month} {period_start.day}-{period_end.day} {period_start.year}"

    scoped_queries = [PlannedQuery(query=scoped_query_text, include_domains=[domain]) for domain in HIGH_SIGNAL_SITES]

    return [open_query, *scoped_queries]
