from datetime import date
from typing import Protocol

from services.market_recap.schemas import Candidate


class SearchProvider(Protocol):
    def search(
        self,
        query: str,
        period_start: date,
        period_end: date,
        include_domains: list[str] | None = None,
    ) -> list[Candidate]: ...
