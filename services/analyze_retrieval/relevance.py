"""Company relevance filter and title dedupe for Brave candidates. Pure functions."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TypeVar
from urllib.parse import urlsplit

from services.analyze_retrieval.schemas import AnalyzePassage, AnalyzeSource
from services.market_recap.schemas import Candidate

T = TypeVar("T")

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
# Legal-form words dropped from the end of a company name ("Amazon.com, Inc." -> "amazon").
_NAME_SUFFIXES = {
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "company",
    "ltd",
    "limited",
    "plc",
    "llc",
    "lp",
    "holdings",
    "holding",
    "group",
    "sa",
    "ag",
    "nv",
    "se",
    "oyj",
    "abp",
    "asa",
    "jsc",
    "com",
    "class",
    "a",
    "b",
    "c",
}
# Name words too generic to stand alone as an alias ("Ford Motor" -> "ford", not "motor").
_GENERIC_NAME_WORDS = {
    "the",
    "and",
    "of",
    "advanced",
    "america",
    "american",
    "bank",
    "brands",
    "capital",
    "chase",
    "communications",
    "devices",
    "digital",
    "electric",
    "energy",
    "entertainment",
    "financial",
    "first",
    "foods",
    "general",
    "global",
    "health",
    "industries",
    "international",
    "materials",
    "media",
    "micro",
    "motor",
    "motors",
    "national",
    "networks",
    "new",
    "partners",
    "platforms",
    "power",
    "products",
    "resources",
    "semiconductor",
    "services",
    "software",
    "solutions",
    "systems",
    "technologies",
    "technology",
    "trust",
    "united",
    "wholesale",
}
# Snippet mentions needed when the title doesn't name the company: one passing mention is usually an
# article about something else ("Turkey cuts interest rates ..." citing JPMorgan once).
_MIN_BODY_MENTIONS = 2
_MIN_ON_TOPIC = 2
# Exchange-qualified listing: "(NASDAQ: COKE)", "(NYSE:KO)".
_LISTING_RE = re.compile(
    r"\((?:NYSE|NASDAQ|Nasdaq|NYSEARCA|NYSE American|AMEX|OTC|TSX|LSE)\s*:\s*([A-Z][A-Z0-9.]{0,6})\)"
)
# Trailing publisher tags: "... - Bloomberg", "... | Reuters", "... By Investing.com".
_TITLE_PUBLISHER_SUFFIX_RE = re.compile(r"\s+(?:[-|–—]\s+[^-|–—]{1,40}|[Bb]y\s+[A-Z][\w.]*(?:\s+[A-Z][\w.]*)?)$")


def _normalize(text: str) -> str:
    return " ".join(_NON_ALNUM_RE.split(text.lower())).strip()


def company_aliases(company_name: str | None, ticker: str | None) -> set[str]:
    """Normalized names a company goes by: the core name plus its distinctive words.
    Empty when no real name is known (missing, or just the ticker)."""
    if not company_name or _normalize(company_name) == _normalize(ticker or ""):
        return set()
    words = _normalize(company_name).split()
    if words and words[0] == "the":
        words = words[1:]
    while len(words) > 1 and words[-1] in _NAME_SUFFIXES:
        words = words[:-1]
    if not words:
        return set()
    aliases = {" ".join(words)}
    aliases.update(word for word in words if len(word) >= 3 and word not in _GENERIC_NAME_WORDS)
    return aliases


def _ticker_symbol(ticker: str | None) -> str:
    symbol = (ticker or "").strip().upper().split(".")[0]
    return symbol if len(symbol) >= 2 and symbol not in {"UNKNOWN", "UNDEFINED", "NULL", "NONE"} else ""


def _alias_pattern(aliases: set[str]) -> re.Pattern[str]:
    """Matches an alias in normalized text, capturing the next word.
    Longest first, so "coca cola" is one mention rather than "coca cola" + "coca" + "cola"."""
    alternation = "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{alternation})(?![a-z0-9])(?: ([a-z0-9]+))?")


def _sibling_words(text: str, aliases: set[str], symbol: str) -> set[str]:
    """Capitalized words that turn the name into another listed company:
    "Coca-Cola Consolidated, Inc. (NASDAQ: COKE)" -> {"consolidated"} when we are KO.
    Share classes ("Alphabet Inc. (NASDAQ: GOOG)") add nothing."""
    words: set[str] = set()
    for listing in _LISTING_RE.finditer(text):
        if listing.group(1).split(".")[0] == symbol:
            continue
        before = text[max(0, listing.start() - 80) : listing.start()]
        for alias in aliases:
            name = r"[^A-Za-z0-9]+".join(re.escape(word) for word in alias.split())
            match = re.search(
                rf"(?<![A-Za-z0-9])(?i:{name})[^A-Za-z0-9]+([A-Z][A-Za-z0-9]*)((?:[^A-Za-z0-9]+[A-Za-z]+)*)[^A-Za-z0-9]*$",
                before,
            )
            if not match or match.group(1).lower() in _NAME_SUFFIXES:
                continue
            if all(word.lower() in _NAME_SUFFIXES for word in re.findall(r"[A-Za-z]+", match.group(2))):
                words.add(match.group(1).lower())
    return words


def _count_mentions(text: str, alias_re: re.Pattern[str] | None, symbol: str, sibling_words: set[str]) -> int:
    count = 0
    if alias_re is not None:
        count += sum(1 for m in alias_re.finditer(_normalize(text)) if m.group(1) not in sibling_words)
    if symbol:
        count += len(re.findall(rf"(?<![A-Za-z0-9]){re.escape(symbol)}(?![A-Za-z0-9])", text))
    return count


def _url_has_ticker(url: str, symbol: str) -> bool:
    # Short symbols ("KO", "F") collide with path words like "/ko/" locales.
    if len(symbol) < 3:
        return False
    return symbol.lower() in _NON_ALNUM_RE.split(urlsplit(url).path.lower())


def is_about_company(candidate: Candidate, *, aliases: set[str], ticker: str | None) -> bool:
    symbol = _ticker_symbol(ticker)
    alias_re = _alias_pattern(aliases) if aliases else None
    siblings = _sibling_words(f"{candidate.title}\n{candidate.raw_content}", aliases, symbol)
    if _count_mentions(candidate.title, alias_re, symbol, siblings) > 0 or _url_has_ticker(candidate.url, symbol):
        return True
    return _count_mentions(candidate.raw_content, alias_re, symbol, siblings) >= _MIN_BODY_MENTIONS


def filter_off_topic(
    candidates: list[Candidate], *, company_name: str | None, ticker: str | None
) -> tuple[list[Candidate], int]:
    """Drop candidates that aren't about the company. Returns (kept, dropped count).
    Keeps everything when the name is unknown or fewer than 2 would be left."""
    aliases = company_aliases(company_name, ticker)
    if not aliases:
        return candidates, 0
    on_topic = [c for c in candidates if is_about_company(c, aliases=aliases, ticker=ticker)]
    if len(on_topic) < _MIN_ON_TOPIC:
        return candidates, 0
    return on_topic, len(candidates) - len(on_topic)


def normalize_title(title: str) -> str:
    """Dedupe key: lowercase alphanumerics without the trailing publisher tag."""
    return _normalize(_TITLE_PUBLISHER_SUFFIX_RE.sub("", title.strip()))


def dedupe_by_title(items: list[T], *, title: Callable[[T], str], better: Callable[[T, T], bool]) -> list[T]:
    """One item per normalized title, at the first one's position; `better(new, kept)` swaps in a better copy.
    Items with an empty title are kept as-is."""
    kept: list[T] = []
    index_by_key: dict[str, int] = {}
    for item in items:
        key = normalize_title(title(item))
        if not key:
            kept.append(item)
            continue
        index = index_by_key.get(key)
        if index is None:
            index_by_key[key] = len(kept)
            kept.append(item)
        elif better(item, kept[index]):
            kept[index] = item
    return kept


def merge_sources(
    groups: list[tuple[list[AnalyzeSource], list[AnalyzePassage]]],
) -> tuple[list[AnalyzeSource], list[AnalyzePassage]]:
    """Concatenate per-ticker retrievals without repeating a source (same id or title) or its passages."""
    sources: list[AnalyzeSource] = []
    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    for group_sources, _ in groups:
        for source in group_sources:
            key = normalize_title(source.title)
            if source.id in seen_ids or (key and key in seen_titles):
                continue
            seen_ids.add(source.id)
            if key:
                seen_titles.add(key)
            sources.append(source)
    passages: list[AnalyzePassage] = []
    seen_passages: set[tuple[str, int]] = set()
    for _, group_passages in groups:
        for passage in group_passages:
            passage_key = (passage.source_id, passage.passage_index)
            if passage.source_id in seen_ids and passage_key not in seen_passages:
                seen_passages.add(passage_key)
                passages.append(passage)
    return sources, passages
