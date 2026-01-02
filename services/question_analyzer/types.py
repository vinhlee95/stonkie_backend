"""Type definitions for question analysis."""

from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import List, Optional


class QuestionType(Enum):
    """Types of questions the system can handle."""

    GENERAL_FINANCE = "general-finance"
    COMPANY_GENERAL = "company-general"
    COMPANY_SPECIFIC_FINANCE = "company-specific-finance"


class FinancialDataRequirement(StrEnum):
    """Level of financial data needed to answer a question."""

    NONE = "none"  # Can be answered without financial data
    BASIC = "basic"  # Needs only fundamental data (market cap, P/E, etc.)
    DETAILED = "detailed"  # Requires full financial statements
    QUARTERLY_SUMMARY = "quarterly_summary"  # Requires summary of recent quarterly report
    ANNUAL_SUMMARY = "annual_summary"  # Requires summary of recent annual report


@dataclass(frozen=True)
class FinancialPeriodRequirement:
    """Specifies which financial periods are needed to answer a question."""

    period_type: str  # "annual", "quarterly", or "both"
    specific_years: Optional[List[int]] = None  # e.g., [2023, 2024]
    specific_quarters: Optional[List[str]] = None  # e.g., ["2024-Q1", "2024-Q2"]
    num_periods: Optional[int] = None  # Number of recent periods


@dataclass(frozen=True)
class AnalysisChunk:
    """A chunk of analysis response."""

    type: str  # "thinking_status", "answer", "related_question", "google_search_ground"
    body: str
    url: Optional[str] = None  # Only for google_search_ground type
