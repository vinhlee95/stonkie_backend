"""Context builders for financial analysis prompts."""

from typing import Dict, Type

from ..types import FinancialDataRequirement
from .base import ContextBuilder, ContextBuilderInput
from .basic_builder import BasicContextBuilder
from .components import validate_section_titles
from .detailed_builder import DetailedContextBuilder
from .none_builder import NoneContextBuilder
from .quarterly_builder import QuarterlyContextBuilder

CONTEXT_BUILDERS: Dict[FinancialDataRequirement, Type[ContextBuilder]] = {
    FinancialDataRequirement.NONE: NoneContextBuilder,
    FinancialDataRequirement.BASIC: BasicContextBuilder,
    FinancialDataRequirement.QUARTERLY_SUMMARY: QuarterlyContextBuilder,
    FinancialDataRequirement.DETAILED: DetailedContextBuilder,
}


def get_context_builder(requirement: FinancialDataRequirement) -> ContextBuilder:
    """Get the appropriate context builder for a data requirement level."""
    builder_class = CONTEXT_BUILDERS.get(requirement)
    if not builder_class:
        raise ValueError(f"No builder for requirement: {requirement}")
    return builder_class()


__all__ = [
    "ContextBuilder",
    "ContextBuilderInput",
    "get_context_builder",
    "validate_section_titles",
    "CONTEXT_BUILDERS",
    "NoneContextBuilder",
    "BasicContextBuilder",
    "QuarterlyContextBuilder",
    "DetailedContextBuilder",
]
