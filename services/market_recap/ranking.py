from datetime import UTC

from services.market_recap.schemas import Candidate
from services.market_recap.source_policy import is_allowlisted


def dedupe(candidates: list[Candidate]) -> list[Candidate]:
    by_source_id: dict[str, Candidate] = {}
    for candidate in candidates:
        current = by_source_id.get(candidate.source_id)
        if current is None or candidate.score > current.score:
            by_source_id[candidate.source_id] = candidate
    return list(by_source_id.values())


def _published_timestamp(candidate: Candidate) -> float:
    published_date = candidate.published_date
    if published_date is None:
        return float("-inf")
    if published_date.tzinfo is None:
        published_date = published_date.replace(tzinfo=UTC)
    return published_date.timestamp()


def rank(candidates: list[Candidate], market: str = "US") -> list[Candidate]:
    return sorted(
        candidates,
        key=lambda candidate: (
            not is_allowlisted(candidate.url, market=market),
            -_published_timestamp(candidate),
            -candidate.score,
            candidate.canonical_url,
        ),
    )
