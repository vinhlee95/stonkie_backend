"""Type definitions for ETF question analysis."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Optional

from connectors.etf_fundamental import ETFFundamentalDto


class ETFQuestionType(StrEnum):
    """Types of ETF-related questions the system can handle."""

    GENERAL_ETF = "general_etf"  # General ETF education questions
    ETF_OVERVIEW = "etf_overview"  # Basic ETF information questions
    ETF_DETAILED_ANALYSIS = "etf_detailed_analysis"  # Complex ETF analysis questions
    ETF_COMPARISON = "etf_comparison"  # Multi-ETF comparison questions


class ETFDataRequirement(StrEnum):
    """Level of ETF data needed to answer a question."""

    NONE = "none"  # Can answer without ETF data (general education)
    BASIC = "basic"  # Needs only core metadata (name, TER, provider, index)
    DETAILED = "detailed"  # Requires full data (holdings, sectors, countries)


@dataclass(frozen=True)
class ETFAnalysisContext:
    """Context passed between ETF analysis components."""

    ticker: str
    question: str
    question_type: ETFQuestionType
    data_requirement: ETFDataRequirement
    etf_data: Optional[ETFFundamentalDto]
    use_google_search: bool
    use_url_context: bool
    deep_analysis: bool
    preferred_model: str
    conversation_messages: Optional[list] = None
    source_url: Optional[str] = None


@dataclass(frozen=True)
class ETFComparisonContext:
    """Context for multi-ETF comparison analysis."""

    tickers: list[str]
    question: str
    etf_data_list: list[ETFFundamentalDto]
    use_google_search: bool
    preferred_model: str
    conversation_messages: Optional[list] = None
