"""Recent headlines per holding from Yahoo news search: queries, parsing, Redis cache entry format and
merging across holdings. PortfolioService does the I/O."""

import logging
from datetime import UTC, datetime

from services.portfolio.chat_targets import base_symbol, mentioned_holdings

logger = logging.getLogger(__name__)

NEWS_TTL_SECONDS = 45 * 60
# Holdings Yahoo has no news for (most ETFs) are asked again sooner, in case a story lands.
EMPTY_NEWS_TTL_SECONDS = 15 * 60
# Stories asked per Yahoo search; the endpoint returns at most MAX_LIMIT across all holdings.
STORIES_PER_QUERY = 10
DEFAULT_LIMIT = 8
MAX_LIMIT = 20
MAX_WORKERS = 8
_FIELDS = ("headline", "source", "url", "published_at", "related")


def cache_key(ticker: str) -> str:
    # Bump the version when is_about changes: entries hold already-filtered stories.
    return f"portfolio_news:v1:{ticker}"


def queries(ticker: str, name: str | None) -> list[str]:
    """Searches to try in order until one finds stories. Yahoo news search knows US symbols but not
    exchange-suffixed ones ("SAP.DE", "RR.L"), so those fall back to the company name. Not the bare
    symbol: "RR" is a different US company."""
    if base_symbol(ticker) != ticker and name:
        return [ticker, name]
    return [ticker]


def parse(raw: list[dict]) -> list[dict]:
    """Yahoo stories as cache items; videos and stories missing a title, link or time are dropped."""
    items = []
    for story in raw:
        if not isinstance(story, dict) or story.get("type", "STORY") != "STORY":
            continue
        title, link, published = story.get("title"), story.get("link"), story.get("providerPublishTime")
        if not (isinstance(title, str) and title.strip() and isinstance(link, str) and link.startswith("http")):
            continue
        if not isinstance(published, int | float) or isinstance(published, bool):
            continue
        related = story.get("relatedTickers")
        items.append(
            {
                "headline": title.strip(),
                "source": story.get("publisher") or "",
                "url": link,
                "published_at": datetime.fromtimestamp(published, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "related": [t for t in related if isinstance(t, str)] if isinstance(related, list) else [],
            }
        )
    return items


def is_about(item: dict, ticker: str, name: str | None) -> bool:
    """Yahoo tags a story with every symbol it touches (an Amazon story is tagged NVDA), so a story is
    about a holding only when tagged with it and its headline names it ("Nvidia", "NVDA"). Listings
    abroad are often tagged by their US symbol ("SAP" for SAP.DE)."""
    if not {ticker, base_symbol(ticker)} & set(item["related"]):
        return False
    return bool(mentioned_holdings(item["headline"], [{"ticker": ticker, "name": name}]))


def from_cache(ticker: str, cached: dict | None) -> list[dict] | None:
    """The cached items, or None on a miss / invalid entry."""
    if cached is None:
        return None
    items = cached.get("items") if isinstance(cached, dict) else None
    if not isinstance(items, list) or not all(isinstance(i, dict) and set(i) >= set(_FIELDS) for i in items):
        logger.warning("Invalid cached news for %s", ticker)
        return None
    return items


def to_json(items: list[dict]) -> dict:
    return {"items": items}


def merge(per_ticker: dict[str, list[dict]], names: dict[str, str | None], limit: int) -> list[dict]:
    """One list across holdings, newest first: a story found for several holdings appears once, tagged
    with every holding it is about (in holding order)."""
    held = list(per_ticker)
    by_url: dict[str, dict] = {}
    for items in per_ticker.values():
        for item in items:
            by_url.setdefault(item["url"], item)
    stories = sorted(by_url.values(), key=lambda i: i["published_at"], reverse=True)[:limit]
    return [
        {
            "headline": item["headline"],
            "source": item["source"],
            "url": item["url"],
            "published_at": item["published_at"],
            "tickers": [t for t in held if is_about(item, t, names.get(t))],
        }
        for item in stories
    ]
