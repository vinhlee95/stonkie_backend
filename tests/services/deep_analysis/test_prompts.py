"""Tests for deep analysis system prompt builder."""

from datetime import date
from unittest.mock import patch

from services.deep_analysis.prompts import build_system_prompt


class TestBuildSystemPrompt:
    def test_contains_persona(self):
        result = build_system_prompt(ticker="AAPL", company_name="Apple Inc.", has_url=False)
        assert "financial analyst" in result.lower()

    def test_contains_date(self):
        with patch("services.deep_analysis.prompts.date") as mock_date:
            mock_date.today.return_value = date(2026, 5, 16)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            result = build_system_prompt(ticker="AAPL", company_name="Apple Inc.", has_url=False)
        assert "May 16, 2026" in result

    def test_contains_tool_descriptions(self):
        result = build_system_prompt(ticker="AAPL", company_name="Apple Inc.", has_url=False)
        assert "brave_search" in result
        assert "get_financial_data" in result
        assert "get_company_profile" in result

    def test_contains_budget(self):
        result = build_system_prompt(ticker="AAPL", company_name="Apple Inc.", has_url=False)
        assert "10 tool calls" in result

    def test_contains_ticker(self):
        result = build_system_prompt(ticker="NVDA", company_name="NVIDIA Corporation", has_url=False)
        assert "NVDA" in result
        assert "NVIDIA Corporation" in result

    def test_with_url(self):
        result = build_system_prompt(ticker="AAPL", company_name="Apple Inc.", has_url=True)
        assert "read_url" in result
        # Should have specific URL-reading instruction
        assert "url" in result.lower()

    def test_without_url(self):
        result = build_system_prompt(ticker="AAPL", company_name="Apple Inc.", has_url=False)
        # read_url tool should still be mentioned (available tools) but no "read the provided URL first" instruction
        # The key difference: no instruction to prioritize reading a URL
        assert "read the provided url first" not in result.lower()

    def test_language_instruction(self):
        result = build_system_prompt(ticker="AAPL", company_name="Apple Inc.", has_url=False)
        assert "language" in result.lower()
