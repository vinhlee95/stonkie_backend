"""Base classes for ETF context builders."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from connectors.etf_fundamental import ETFFundamentalDto


@dataclass
class ETFContextBuilderInput:
    """Input data for building ETF context prompts."""

    ticker: str
    question: str
    etf_data: Optional[ETFFundamentalDto]
    use_google_search: bool
    deep_analysis: bool
    source_url: Optional[str] = None


class ETFContextBuilder(ABC):
    """Abstract base class for ETF context builders."""

    @abstractmethod
    def build(self, input: ETFContextBuilderInput) -> str:
        """
        Build the ETF context prompt.

        Args:
            input: The input data for building the context

        Returns:
            Formatted prompt string with ETF context
        """
        pass
