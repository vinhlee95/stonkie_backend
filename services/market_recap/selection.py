"""Pick the sources a market recap is written from.

Steps run in priority order, so wraps take publisher slots before stories do:

    ranked ─► facts ─► eligible ─► one wrap per trading day ─► on-topic stories ─► backfill to the minimum

`top_k` is a ceiling, not a quota. Weak pages, pages dated outside the period and past-the-cap repeats are used
only to reach `MIN_SOURCES`.
"""

import logging
from collections import Counter
from datetime import date, timedelta

from services.market_recap.criteria import CandidateFacts, candidate_facts, is_eligible, policy_for
from services.market_recap.logging import log_event
from services.market_recap.schemas import Candidate
from services.market_recap.source_policy import registrable_domain

logger = logging.getLogger(__name__)

EVENT_SOURCES_SELECTED = "recap.sources.selected"
MAX_SOURCES_PER_PUBLISHER = 2
MIN_SOURCES = 5  # Below this, repeats and weaker pages are better than a recap with too little to work from.

Pool = list[tuple[Candidate, CandidateFacts]]


def select_candidates(
    ranked: list[Candidate],
    *,
    market: str,
    period_start: date,
    period_end: date,
    top_k: int,
) -> list[Candidate]:
    selection = Selection(max_sources=top_k, per_publisher=MAX_SOURCES_PER_PUBLISHER)
    minimum = min(top_k, MIN_SOURCES)

    if not policy_for(market).balanced_selection:
        for candidate in ranked[:minimum]:
            selection.add(candidate, "top")
    else:
        facts = {c.source_id: candidate_facts(c, market=market, period_end=period_end) for c in ranked}
        pool = [(c, facts[c.source_id]) for c in ranked if is_eligible(facts[c.source_id], period_start, period_end)]
        _add_one_wrap_per_trading_day(pool, selection, period_start, period_end)
        _add_on_topic_stories(pool, selection)
        _backfill_in_rank_order(ranked, selection, up_to=minimum)

    log_event(
        logger,
        EVENT_SOURCES_SELECTED,
        {"market": market.upper(), "count": len(selection), "sources": selection.log_entries()},
    )
    return selection.candidates


class Selection:
    """Picked sources in output order, with why each was picked. Steps check `accepts` to honour the limits."""

    def __init__(self, *, max_sources: int, per_publisher: int) -> None:
        self.candidates: list[Candidate] = []
        self._reasons: list[str] = []
        self._ids: set[str] = set()
        self._publisher_counts: Counter[str] = Counter()
        self._max_sources = max_sources
        self._per_publisher = per_publisher

    def __len__(self) -> int:
        return len(self.candidates)

    def contains(self, candidate: Candidate) -> bool:
        return candidate.source_id in self._ids

    def publisher_count(self, candidate: Candidate) -> int:
        return self._publisher_counts[registrable_domain(candidate.url)]

    def accepts(self, candidate: Candidate) -> bool:
        return (
            len(self) < self._max_sources
            and not self.contains(candidate)
            and self.publisher_count(candidate) < self._per_publisher
        )

    def add(self, candidate: Candidate, reason: str, *, at: int | None = None) -> None:
        index = len(self) if at is None else at
        self.candidates.insert(index, candidate)
        self._reasons.insert(index, reason)
        self._ids.add(candidate.source_id)
        self._publisher_counts[registrable_domain(candidate.url)] += 1

    def log_entries(self) -> list[str]:
        return [f"{reason} {registrable_domain(c.url)}" for reason, c in zip(self._reasons, self.candidates)]


def _add_one_wrap_per_trading_day(pool: Pool, selection: Selection, period_start: date, period_end: date) -> None:
    """At most one session wrap per trading day, emitted oldest first.

    The latest day picks first: it gets first choice of publisher slots, and in a multi-day period its wrap should
    carry the week's totals. Within a day: week totals (first pick only), then least-used publisher, then longest.
    """
    start = len(selection)
    for day in reversed(_trading_days(period_start, period_end)):
        options = [
            (candidate, facts)
            for candidate, facts in pool
            if facts.is_session_wrap and facts.session_date == day and selection.accepts(candidate)
        ]
        if not options:
            continue
        wants_week_total = period_end > period_start and len(selection) == start
        chosen, _ = min(
            options,
            key=lambda option: (
                wants_week_total and not option[1].reports_week_total,
                selection.publisher_count(option[0]),
                -option[1].content_chars,
            ),
        )
        selection.add(chosen, f"wrap:{day.isoformat()}", at=start)


def _add_on_topic_stories(pool: Pool, selection: Selection) -> None:
    for candidate, facts in pool:
        if facts.is_on_topic_story and selection.accepts(candidate):
            selection.add(candidate, "story")


def _backfill_in_rank_order(ranked: list[Candidate], selection: Selection, *, up_to: int) -> None:
    """Fill up to `up_to` from the whole ranked list, ignoring eligibility and the publisher cap."""
    for candidate in ranked:
        if len(selection) >= up_to:
            return
        if not selection.contains(candidate):
            selection.add(candidate, "backfill")


def _trading_days(period_start: date, period_end: date) -> list[date]:
    """Weekdays in the period, or every day when it has none (a weekend-only period)."""
    days = [period_start + timedelta(days=offset) for offset in range((period_end - period_start).days + 1)]
    return [day for day in days if day.weekday() < 5] or days
