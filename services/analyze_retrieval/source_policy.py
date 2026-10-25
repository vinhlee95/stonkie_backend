"""Chat-specific source policy for analyze v2 (Brave-backed retrieval).

Independent of services.market_recap.source_policy. Two markets (GLOBAL, VN)
plus an FI extension that stacks on GLOBAL by tier number. Hard DISCARDS take
precedence over any allowlist tier. The *.gov wildcard counts as one TIER_1
entry for GLOBAL/FI but is not honored for VN.

Asset class: an ETF extension stacks on the market's tiers by tier number, only
when the caller passes asset_class="etf". Stock (the default) is unaffected.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

Market = Literal["GLOBAL", "VN", "FI"]
AssetClass = Literal["stock", "etf"]

_VN_MULTI_SUFFIXES = (".com.vn", ".gov.vn", ".org.vn", ".net.vn", ".edu.vn")

# Locked policy from PRD analyze-v2-brave-migration-prd.json (locked_policy).
# Counts asserted by tests: GLOBAL T1=16, T2=20; VN T1=10, T2=13; FI ext T1=2, T2=3.
# The literal "*.gov" entry occupies one of GLOBAL_TIER_1's 16 slots; matching is
# handled via GLOBAL_TIER_1_WILDCARDS at runtime.
GLOBAL_TIER_1: frozenset[str] = frozenset(
    {
        "sec.gov",
        "*.gov",
        "companieshouse.gov.uk",
        "sedarplus.ca",
        "europa.eu",
        "reuters.com",
        "bloomberg.com",
        "ft.com",
        "wsj.com",
        "cnbc.com",
        "apnews.com",
        "nytimes.com",
        "axios.com",
        "barrons.com",
        "economist.com",
        "marketwatch.com",
    }
)

GLOBAL_TIER_1_WILDCARDS: tuple[str, ...] = (".gov",)

GLOBAL_TIER_2: frozenset[str] = frozenset(
    {
        "investing.com",
        "finance.yahoo.com",
        "morningstar.com",
        "stockanalysis.com",
        "macrotrends.net",
        "simplywall.st",
        "seekingalpha.com",
        "ishares.com",
        "vanguard.com",
        "ssga.com",
        "invesco.com",
        "blackrock.com",
        "schwab.com",
        "fidelity.com",
        "am.jpmorgan.com",
        "statestreet.com",
        "nasdaq.com",
        "investopedia.com",
        "etf.com",
        "tradingeconomics.com",
        "idc.com",
        "canalys.com",
        "counterpointresearch.com",
        "statista.com",
    }
)

FI_EXTENSION_TIER_1: frozenset[str] = frozenset({"inderes.fi", "kauppalehti.fi"})
FI_EXTENSION_TIER_2: frozenset[str] = frozenset({"hs.fi", "yle.fi", "arvopaperi.fi"})

# ETF extension (asset_class="etf" only). Tier 1: fund issuers' own sites (factsheets, holdings, TER) and
# justETF (issuer-sourced UCITS ETF profiles). Tier 2: third-party ETF research/data. Matching is by host
# suffix (paths are ignored), so DWS Xtrackers is the etf.dws.com host rather than etf.dws.com/xtrackers.
# The extension wins by tier number: ishares/vanguard/ssga are GLOBAL tier 2 but ETF tier 1.
ETF_EXTENSION_TIER_1: frozenset[str] = frozenset(
    {"justetf.com", "ishares.com", "vanguard.com", "ssga.com", "amundietf.com", "etf.dws.com"}
)
ETF_EXTENSION_TIER_2: frozenset[str] = frozenset({"morningstar.com", "etf.com"})

VN_TIER_1: frozenset[str] = frozenset(
    {
        "hsx.vn",
        "hnx.vn",
        "ssc.gov.vn",
        "sbv.gov.vn",
        "cafef.vn",
        "vneconomy.vn",
        "thitruongtaichinhtiente.vn",
        "vietstock.vn",
        "vir.com.vn",
        "baodautu.vn",
        "nhandan.vn",
    }
)

VN_TIER_2: frozenset[str] = frozenset(
    {
        "tinnhanhchungkhoan.vn",
        "vietnamplus.vn",
        "thesaigontimes.vn",
        "dnse.com.vn",
        "bnews.vn",
        "ssi.com.vn",
        "vndirect.com.vn",
        "vcsc.com.vn",
        "hsc.com.vn",
        "bsc.com.vn",
        "mbs.com.vn",
        "fpts.com.vn",
    }
)

DISCARDS: frozenset[str] = frozenset(
    {
        "reddit.com",
        "x.com",
        "twitter.com",
        "youtube.com",
        "facebook.com",
        "linkedin.com",
        "quora.com",
        "medium.com",
        "substack.com",
    }
)

# Path-prefix discards: (registrable_domain, path_prefix). Both must match.
DISCARD_PATH_PREFIXES: tuple[tuple[str, str], ...] = (("tradingview.com", "/ideas"),)


def registrable_domain(url: str) -> str:
    """Return the registrable domain for a URL.

    Handles Vietnamese multi-part suffixes; falls back to the last two labels
    otherwise. Returns "" for inputs without a hostname.
    """
    # Why: kept public for Phase 1 publisher.py (publisher_label_for) which keys
    # off registrable_domain. tier_for/is_discarded use host-suffix matching
    # instead because last-2-labels misses multi-part TLDs like .gov.uk.
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


def _hostname(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _host_in_set(url: str, domains: frozenset[str]) -> bool:
    """True if the URL's hostname matches any registrable domain in `domains`.

    Match = exact host or host endswith '.<domain>'. Wildcard literals like
    "*.gov" are skipped here (handled by _matches_wildcard).
    """
    host = _hostname(url)
    if not host:
        return False
    for d in domains:
        if d.startswith("*."):
            continue
        if host == d or host.endswith("." + d):
            return True
    return False


def _matches_wildcard(url: str, suffixes: tuple[str, ...]) -> bool:
    host = _hostname(url)
    if not host:
        return False
    for suffix in suffixes:
        if host.endswith(suffix):
            return True
    return False


def is_discarded(url: str) -> bool:
    if _host_in_set(url, DISCARDS):
        return True
    host = _hostname(url)
    path = urlsplit(url).path or ""
    for domain, path_prefix in DISCARD_PATH_PREFIXES:
        if (host == domain or host.endswith("." + domain)) and path.startswith(path_prefix):
            return True
    return False


def tier_for(url: str, market: Market, asset_class: AssetClass = "stock") -> int | None:
    """Return 1 (boost=4), 2 (boost=2), or None (untrusted).

    Discards always win. VN does not inherit GLOBAL and does not honor the
    *.gov wildcard. FI stacks GLOBAL ∪ FI_EXTENSION by tier number. For
    asset_class="etf" the ETF extension stacks on the market's tiers by tier number.
    """
    if is_discarded(url):
        return None
    if not _hostname(url):
        return None

    if asset_class == "etf":
        if _host_in_set(url, ETF_EXTENSION_TIER_1):
            return 1
        tier = _market_tier(url, market)
        if tier is None and _host_in_set(url, ETF_EXTENSION_TIER_2):
            return 2
        return tier
    return _market_tier(url, market)


def _market_tier(url: str, market: Market) -> int | None:
    if market == "VN":
        if _host_in_set(url, VN_TIER_1):
            return 1
        if _host_in_set(url, VN_TIER_2):
            return 2
        return None

    # GLOBAL or FI
    if _host_in_set(url, GLOBAL_TIER_1):
        return 1
    if market == "FI" and _host_in_set(url, FI_EXTENSION_TIER_1):
        return 1
    if _matches_wildcard(url, GLOBAL_TIER_1_WILDCARDS):
        return 1
    if _host_in_set(url, GLOBAL_TIER_2):
        return 2
    if market == "FI" and _host_in_set(url, FI_EXTENSION_TIER_2):
        return 2
    return None


def is_trusted(url: str, market: Market, asset_class: AssetClass = "stock") -> bool:
    return tier_for(url, market, asset_class) is not None
