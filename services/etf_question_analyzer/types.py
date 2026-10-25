"""Type definitions for ETF question analysis."""

from enum import StrEnum


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
