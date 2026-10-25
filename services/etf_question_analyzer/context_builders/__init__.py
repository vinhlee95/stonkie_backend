"""ETF context builders for prompt construction."""

from services.etf_question_analyzer.types import ETFDataRequirement

from .base import ETFContextBuilder, ETFContextBuilderInput
from .basic_builder import BasicETFBuilder
from .comparison_builder import ComparisonContextBuilderInput, ComparisonETFBuilder
from .components import ETFPromptComponents
from .detailed_builder import DetailedETFBuilder
from .none_builder import NoneETFBuilder

__all__ = [
    "ETFContextBuilder",
    "ETFContextBuilderInput",
    "ETFPromptComponents",
    "NoneETFBuilder",
    "BasicETFBuilder",
    "DetailedETFBuilder",
    "ComparisonETFBuilder",
    "ComparisonContextBuilderInput",
    "get_etf_context_builder",
]


def get_etf_context_builder(requirement: ETFDataRequirement) -> ETFContextBuilder:
    """
    Factory function to get appropriate ETF context builder.

    Args:
        requirement: The data requirement level

    Returns:
        Appropriate ETFContextBuilder instance
    """
    if requirement == ETFDataRequirement.NONE:
        return NoneETFBuilder()
    elif requirement == ETFDataRequirement.BASIC:
        return BasicETFBuilder()
    elif requirement == ETFDataRequirement.DETAILED:
        return DetailedETFBuilder()
    else:
        return BasicETFBuilder()
