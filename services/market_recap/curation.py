"""Let an LLM choose the recap's sources from the filtered pool, while code keeps a few guarantees.

    pool ─► catalog (S1.., by session date) ─► one LLM call: ranked picks ─► top_k ─► last-day wrap guarantee

The curator only names catalog IDs, so it can't cite a page outside the pool. It decides how many sources the recap
needs and how to mix publishers (a goal in the prompt, not a limit in code: the rules' 2-per-publisher limit cut its
US picks to 6-7 and lost to the rules in blind judging). Fewer than `MIN_CURATED_SOURCES` valid picks is a failure, and
the caller falls back to rule selection.
"""

import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import date

from agent.multi_agent import MultiAgent
from services.market_recap.criteria import candidate_facts, policy_for
from services.market_recap.logging import log_event
from services.market_recap.schemas import Candidate
from services.market_recap.selection import EVENT_SOURCES_SELECTED, check_curator_picks
from services.market_recap.session_dates import session_date, trading_days
from services.market_recap.source_policy import registrable_domain
from services.shared.recap_model import RECAP_MODEL

logger = logging.getLogger(__name__)

MIN_CURATED_SOURCES = 3  # the validator's unique-source floor
EXCERPT_CHARS = 300
# RECAP_MODEL reasons by default and its reasoning counts toward max_tokens: at the client's 8192 default it used
# 7.6k reasoning tokens on a 58-page pool and the answer was cut off. Neither {"effort": "low"} nor a
# {"max_tokens": ...} budget is honoured (2.6k-16.4k reasoning tokens; FI runs hit 16k with no answer), so it's off.
CURATOR_REASONING = {"enabled": False}
CURATOR_MAX_TOKENS = 16_384
_START_TAG = "[CURATOR_JSON]"
_END_TAG = "[/CURATOR_JSON]"
_MAX_ROLE_CHARS = 40
_MAX_WHY_CHARS = 160
_MARKET_BRIEFS = {
    "US": [
        "Market: US stocks. Main indexes: S&P 500, Dow Jones Industrial Average, Nasdaq Composite.",
        "Pages must be in English and about the US stock market or what moved it.",
    ],
    "FI": [
        "Market: Finland. Main index: OMX Helsinki (also OMX Helsinki 25 / OMXH25).",
        "A page must be Finnish market news: the Helsinki index or Helsinki-listed companies. Pan-European wraps "
        "that only mention Helsinki in passing, and pages about non-Finnish companies, don't count.",
        "Prefer English pages; use Finnish ones only when nothing in English covers that day or story.",
    ],
    "VN": [
        "Market: Vietnam. Main index: VN-Index (also HNX-Index, UPCoM). Pages are in Vietnamese.",
        "Outlook pieces (nhận định, dự báo) name VN-Index too but are not wraps: a wrap reports how a session closed.",
        "A good set also covers money flow (foreign net buying/selling, liquidity) and macro context (rates, FX, "
        "policy). Global stories (crypto, US tech) only count when the page ties them to Vietnamese stocks.",
    ],
}
_DEFAULT_MARKET_BRIEF = ["Pages must be about this market's stocks or what moved them."]


class CuratorError(Exception):
    """The curator gave no usable answer; the caller falls back to rule selection."""


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    candidate: Candidate
    session_date: date | None
    publisher: str


@dataclass(frozen=True)
class Pick:
    id: str
    role: str
    why: str


@dataclass(frozen=True)
class CuratorAnswer:
    picks: list[Pick]
    would_search: list[dict]
    dropped: list[tuple[str, str]]  # (pick id, reason) for unknown or repeated IDs
    unknown: list[Pick]  # picks naming an ID outside the catalog, kept for the audit


@dataclass(frozen=True)
class CurationResult:
    candidates: list[Candidate]
    audit: dict


def curate_sources(
    pool: list[Candidate],
    *,
    market: str,
    cadence: str,
    period_start: date,
    period_end: date,
    top_k: int = 10,
    agent: MultiAgent | None = None,
) -> CurationResult:
    if not pool:
        raise CuratorError("empty_pool")
    llm = agent or MultiAgent(model_name=RECAP_MODEL)
    catalog = build_catalog(pool, market=market, period_end=period_end)
    by_id = {entry.id: entry for entry in catalog}

    prompt = build_curator_prompt(
        catalog, market=market, cadence=cadence, period_start=period_start, period_end=period_end, top_k=top_k
    )
    raw_output = _ask(llm, prompt)
    answer = parse_curator_output(raw_output, set(by_id))
    kept, over_limits = _take_within_limits([p.id for p in answer.picks], by_id, top_k=top_k)
    kept, latest_wrap, displaced = _with_latest_day_wrap(
        kept, catalog, market=market, period_start=period_start, period_end=period_end, top_k=top_k
    )
    if len(kept) < MIN_CURATED_SOURCES:
        raise CuratorError(f"too_few_picks: {len(kept)}")

    drop_reasons = {
        **dict(answer.dropped),
        **dict(over_limits),
        **{i: "displaced_by_latest_wrap" for i in displaced},
    }
    roles = {p.id: p.role for p in answer.picks}
    log_event(
        logger,
        EVENT_SOURCES_SELECTED,
        {
            "market": market.upper(),
            "count": len(kept),
            "sources": [
                f"curator:{'latest_wrap' if i == latest_wrap else roles.get(i) or 'pick'} {by_id[i].publisher}"
                for i in kept
            ],
            "method": "curator",
            "would_search": [w.get("query") for w in answer.would_search],
        },
    )
    return CurationResult(
        candidates=[by_id[pick_id].candidate for pick_id in kept],
        audit={
            "method": "curator",
            "model": getattr(llm, "model_name", ""),
            "added_latest_wrap": latest_wrap,
            "displaced_by_latest_wrap": displaced,
            "catalog": [
                {
                    "id": e.id,
                    "url": e.candidate.url,
                    "session_date": e.session_date.isoformat() if e.session_date else None,
                }
                for e in catalog
            ],
            "picks": _audit_picks(answer, by_id, set(kept), drop_reasons),
            "would_search": answer.would_search,
            "raw_output": raw_output,
        },
    )


def _with_latest_day_wrap(
    kept: list[str], catalog: list[CatalogEntry], *, market: str, period_start: date, period_end: date, top_k: int
) -> tuple[list[str], str | None, list[str]]:
    """Make sure the picks say how the period ended; the curator often leaves that out.

    Uses the rule facts (index names, session date, week-total phrases). Needed: a wrap of the last trading day, and in
    a multi-day period one that reports the week's totals when the pool has such a wrap. When the picks lack it, add the
    rules' choice for that day (week totals first, then least-used publisher, then longest) as the last source; at
    `top_k` it displaces the curator's lowest-priority pick. Returns (kept, added id or None, displaced ids).
    """
    if policy_for(market).session_index_re is None:
        return kept, None, []
    latest = trading_days(period_start, period_end)[-1]
    facts = {e.id: candidate_facts(e.candidate, market=market, period_end=period_end) for e in catalog}

    def is_latest_wrap(entry_id: str) -> bool:
        f = facts[entry_id]
        return f.allowlisted and f.is_session_wrap and f.session_date == latest

    multi_day = period_end > period_start
    options = [e.id for e in catalog if is_latest_wrap(e.id)]
    week_total_options = [i for i in options if facts[i].reports_week_total] if multi_day else []
    if week_total_options:
        options = week_total_options  # the week's totals exist in the pool: the recap must report them

    if not options or any(pick_id in options for pick_id in kept):
        return kept, None, []
    by_id = {e.id: e for e in catalog}
    used = Counter(by_id[pick_id].publisher for pick_id in kept)
    chosen = min(
        options,
        key=lambda i: (
            multi_day and not facts[i].reports_week_total,
            used[by_id[i].publisher],
            -facts[i].content_chars,
        ),
    )
    prioritised = [(chosen, by_id[chosen].candidate), *((i, by_id[i].candidate) for i in kept)]
    allowed, _ = check_curator_picks(prioritised, top_k=top_k)
    allowed_ids = {i for i, _ in allowed}
    return [i for i in kept if i in allowed_ids] + [chosen], chosen, [i for i in kept if i not in allowed_ids]


def build_catalog(pool: list[Candidate], *, market: str, period_end: date) -> list[CatalogEntry]:
    """Pages ordered by session date (undated last), publisher, URL; not rank order, so rank's bias isn't passed on."""
    month_first = policy_for(market).month_first_numeric_dates
    dated = [
        (
            session_date(
                title=c.title or "",
                url=c.canonical_url,
                published=c.published_date,
                period_end=period_end,
                month_first_numeric_dates=month_first,
            ),
            registrable_domain(c.url),
            c,
        )
        for c in pool
    ]
    dated.sort(key=lambda item: (item[0] is None, item[0] or date.min, item[1], item[2].canonical_url))
    width = len(str(len(dated))) if len(dated) >= 100 else 1  # S001.. only when the pool reaches 100 pages
    return [
        CatalogEntry(id=f"S{index:0{width}d}", candidate=c, session_date=day, publisher=publisher)
        for index, (day, publisher, c) in enumerate(dated, start=1)
    ]


def build_curator_prompt(
    catalog: list[CatalogEntry],
    *,
    market: str,
    cadence: str,
    period_start: date,
    period_end: date,
    top_k: int = 10,
) -> str:
    daily = cadence.lower() == "daily"
    days = ", ".join(day.isoformat() for day in trading_days(period_start, period_end))
    lines = [
        f"You choose the sources a {'daily' if daily else 'weekly'} stock market recap will be written from. "
        "The writer sees only the pages you pick, so pick for coverage and quality.",
        *_MARKET_BRIEFS.get(market.upper(), _DEFAULT_MARKET_BRIEF),
    ]
    if daily:
        lines += [
            f"Recap: the trading session of {period_start.isoformat()}.",
            "A good set: one or two wraps of that session (how the main index closed; from different publishers), "
            "then at least 3 stories that explain what moved the market that day.",
        ]
    else:
        lines += [
            f"Recap: the week {period_start.isoformat()} to {period_end.isoformat()}. Trading days: {days}.",
            "A good set: one session wrap (how the main index closed) for each trading day the catalog covers; for "
            "the latest day prefer a wrap that also reports the week's move. Then at least 2 stories on the week's "
            "main drivers (earnings, macro data, central bank, policy, big company moves).",
            f"You must include a wrap of the last trading day ({trading_days(period_start, period_end)[-1].isoformat()}"
            "): the recap has to say how the week ended. Prefer one that also reports the week's totals.",
        ]
    lines += [
        "Wraps say what happened; stories explain why. Use the wraps' own explanations to find the drivers (e.g. a "
        "record drop in one stock, an oil move, a jobs report, a Fed signal, an election) and pick a story that "
        "covers each one. Don't spend picks on extra wraps of a day that already has one when a driver has no story.",
        "Avoid: premarket or futures-only pages, previews and next-week outlooks, other markets, non-market topics, "
        "hub/section/quote/video/live pages unless nothing else covers that day, and pages about a session outside "
        "the period. The date shown is a parsed hint and can be wrong: judge from the title and text.",
        f"Limits: at most {top_k} picks, only IDs from the catalog. There is no fixed minimum: pick as many as the "
        f"recap needs to cover the period well (usually 5-{top_k}); fewer than {MIN_CURATED_SOURCES} is not enough.",
        "Prefer a mix of publishers: different outlets explain different drivers, and one outlet's framing shouldn't "
        "dominate the recap. Use several pages from one publisher only when they cover different days or stories "
        "that no other page covers. List picks best first and write each pick's publisher.",
        "In would_search, list anything the catalog is missing for a good recap and the search query you would run "
        "to find it (an empty list if nothing is missing).",
        f"Return only JSON wrapped in {_START_TAG}...{_END_TAG}:",
        '{"picks":[{"id":"S1","publisher":"example.com","role":"wrap:YYYY-MM-DD or story","why":"under 12 words"}],'
        '"would_search":[{"missing":"...","query":"..."}]}',
        "",
        f"Pages per publisher: {_publisher_counts(catalog)}",
        "Catalog (ordered by date, then publisher). Each line: [ID] session date | publisher | title | URL path | "
        "content length, then the start of the content.",
    ]
    lines += [_catalog_line(entry) for entry in catalog]
    return "\n".join(lines)


def parse_curator_output(raw_text: str, catalog_ids: set[str]) -> CuratorAnswer:
    start = raw_text.find(_START_TAG)
    end = raw_text.find(_END_TAG, start + 1)
    if start < 0 or end < 0:
        raise CuratorError("missing_markers")
    try:
        data = json.loads(raw_text[start + len(_START_TAG) : end])
    except json.JSONDecodeError as error:
        raise CuratorError(f"bad_json: {error.msg}") from error
    if not isinstance(data, dict) or not isinstance(data.get("picks"), list):
        raise CuratorError("bad_shape")

    picks: list[Pick] = []
    unknown: list[Pick] = []
    dropped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in data["picks"]:
        pick_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(pick_id, str):
            continue
        pick = Pick(
            id=pick_id,
            role=str(item.get("role") or "")[:_MAX_ROLE_CHARS],
            why=str(item.get("why") or "")[:_MAX_WHY_CHARS],
        )
        if pick_id not in catalog_ids:
            dropped.append((pick_id, "unknown_id"))
            unknown.append(pick)
        elif pick_id in seen:
            dropped.append((pick_id, "duplicate"))
        else:
            seen.add(pick_id)
            picks.append(pick)
    would_search = data.get("would_search")
    return CuratorAnswer(
        picks=picks,
        would_search=[entry for entry in would_search if isinstance(entry, dict)]
        if isinstance(would_search, list)
        else [],
        dropped=dropped,
        unknown=unknown,
    )


def _ask(llm: MultiAgent, prompt: str) -> str:
    try:
        # The stream mixes text with reasoning and annotation dicts; only text is the answer.
        chunks = llm.generate_content(prompt=prompt, reasoning=CURATOR_REASONING, max_tokens=CURATOR_MAX_TOKENS)
        return "".join(chunk for chunk in chunks if isinstance(chunk, str))
    except Exception as error:
        raise CuratorError(f"agent_error: {type(error).__name__}") from error


def _take_within_limits(
    ranked: list[str], by_id: dict[str, CatalogEntry], *, top_k: int
) -> tuple[list[str], list[tuple[str, str]]]:
    """The first `top_k` IDs in pick order, and why the others were dropped."""
    kept, dropped = check_curator_picks([(i, by_id[i].candidate) for i in ranked], top_k=top_k)
    return [pick_id for pick_id, _ in kept], dropped


def _audit_picks(
    answer: CuratorAnswer, by_id: dict[str, CatalogEntry], kept: set[str], drop_reasons: dict[str, str]
) -> list[dict]:
    """Every distinct pick of the final answer, in pick order, then picks naming unknown IDs."""
    return [
        {
            "id": pick.id,
            "url": by_id[pick.id].candidate.url if pick.id in by_id else None,
            "role": pick.role,
            "why": pick.why,
            "kept": pick.id in kept,
            "drop_reason": None if pick.id in kept else drop_reasons.get(pick.id),
        }
        for pick in [*answer.picks, *answer.unknown]
    ]


def _publisher_counts(catalog: list[CatalogEntry]) -> str:
    counts = Counter(entry.publisher for entry in catalog)
    return ", ".join(f"{publisher} {count}" for publisher, count in counts.most_common())


def _catalog_line(entry: CatalogEntry) -> str:
    c = entry.candidate
    day = entry.session_date.isoformat() if entry.session_date else "undated"
    path = c.canonical_url.split("/", 3)[3] if c.canonical_url.count("/") >= 3 else ""
    excerpt = " ".join((c.raw_content or "").split())[:EXCERPT_CHARS]
    return (
        f"[{entry.id}] {day} | {entry.publisher} | {c.title} | /{path} | {len(c.raw_content or '')} chars\n"
        f"    {excerpt}"
    )
