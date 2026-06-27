from services.market_recap.ranking import _published_timestamp, _quality_rank
from services.market_recap.schemas import Candidate
from services.market_recap.source_policy import is_allowlisted

_OFF_TOPIC_PENALTY = 6


def _relevance_terms(ticker: str, company_name: str) -> list[str]:
    terms = [ticker.lower()]
    # Use the distinctive head of the company name (drop corporate suffixes).
    primary = company_name.lower().split(",")[0].strip()
    for suffix in (" inc.", " inc", " corporation", " corp.", " corp", " co.", " company", " plc", " ltd."):
        if primary.endswith(suffix):
            primary = primary[: -len(suffix)].strip()
    if primary:
        terms.append(primary)
    return [term for term in terms if term]


def ticker_relevance_rank(candidate: Candidate, ticker: str, company_name: str) -> int:
    haystack = " ".join(
        [
            (candidate.title or "").lower(),
            (candidate.snippet or "").lower(),
            (candidate.raw_content or "").lower(),
            candidate.canonical_url.lower(),
        ]
    )
    terms = _relevance_terms(ticker, company_name)
    return 0 if any(term in haystack for term in terms) else _OFF_TOPIC_PENALTY


def rank(
    candidates: list[Candidate],
    *,
    ticker: str,
    company_name: str,
    market: str = "US",
) -> list[Candidate]:
    return sorted(
        candidates,
        key=lambda candidate: (
            not is_allowlisted(candidate.url, market=market),
            ticker_relevance_rank(candidate, ticker, company_name),
            _quality_rank(candidate),
            -_published_timestamp(candidate),
            -candidate.score,
            candidate.canonical_url,
        ),
    )
