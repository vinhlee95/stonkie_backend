"""Base classes for context builders."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class ContextBuilderInput:
    """Input data for building financial context prompts."""

    ticker: str
    question: str
    company_fundamental: Optional[Dict[str, Any]]
    annual_statements: List[Dict[str, Any]]
    quarterly_statements: List[Dict[str, Any]]
    dimension_sections: Optional[List[Dict]] = None
    deep_analysis: bool = False
    source_url: Optional[str] = None  # Generic URL field for URL-based analysis


class ContextBuilder(ABC):
    """Abstract base class for context builders."""

    @abstractmethod
    def build(self, input: ContextBuilderInput) -> str:
        """
        Build the financial context prompt.

        Args:
            input: The input data for building the context

        Returns:
            Formatted prompt string with financial context
        """
        pass
