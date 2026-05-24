from datetime import date

import pytest

from services.recap_query_reformulator import RecapQueryReformulator


class TestParseQueries:
    def test_valid_single_query(self):
        raw = '{"queries": ["Buffett Indicator US stock market 2026"], "reasoning": "Added temporal context"}'
        parsed = RecapQueryReformulator._parse_queries(raw)

        assert parsed["queries"] == ["Buffett Indicator US stock market 2026"]
        assert parsed["reasoning"] == "Added temporal context"

    def test_valid_multi_query(self):
        raw = '{"queries": ["Buffett Indicator current value 2026", "total market cap to GDP ratio US"], "reasoning": "Split by angle"}'
        parsed = RecapQueryReformulator._parse_queries(raw)

        assert len(parsed["queries"]) == 2
        assert parsed["queries"][0] == "Buffett Indicator current value 2026"
        assert parsed["queries"][1] == "total market cap to GDP ratio US"

    def test_caps_at_three_queries(self):
        raw = '{"queries": ["q1", "q2", "q3", "q4", "q5"], "reasoning": "many"}'
        parsed = RecapQueryReformulator._parse_queries(raw)

        assert len(parsed["queries"]) == 3

    def test_no_json_raises(self):
        raw = "I cannot reformulate this question."
        with pytest.raises(ValueError, match="No JSON"):
            RecapQueryReformulator._parse_queries(raw)

    def test_empty_list_raises(self):
        raw = '{"queries": [], "reasoning": "empty"}'
        with pytest.raises(ValueError, match="empty"):
            RecapQueryReformulator._parse_queries(raw)

    def test_non_string_items_raise(self):
        raw = '{"queries": [123, true], "reasoning": "wrong types"}'
        with pytest.raises(ValueError, match="string"):
            RecapQueryReformulator._parse_queries(raw)

    def test_json_wrapped_in_markdown_fences(self):
        raw = '```json\n{"queries": ["US market valuation metrics 2026"], "reasoning": "focused"}\n```'
        parsed = RecapQueryReformulator._parse_queries(raw)

        assert parsed["queries"] == ["US market valuation metrics 2026"]


def _make_reformulator(classifier):
    return RecapQueryReformulator(
        market="US",
        period_start=date(2026, 5, 19),
        period_end=date(2026, 5, 22),
        classifier=classifier,
    )


class TestReformulateHappyPath:
    def test_successful_reformulation(self):
        def good_classifier(question, market, period_start, period_end):
            return '{"queries": ["Buffett Indicator current value 2026"], "reasoning": "Added temporal context"}'

        result = _make_reformulator(good_classifier).reformulate("what's current buffet index?")

        assert result.used_fallback is False
        assert result.queries == ["Buffett Indicator current value 2026"]
        assert result.reasoning == "Added temporal context"

    def test_classifier_receives_bound_context(self):
        received = {}

        def capturing_classifier(question, market, period_start, period_end):
            received["question"] = question
            received["market"] = market
            received["period_start"] = period_start
            received["period_end"] = period_end
            return '{"queries": ["test query"], "reasoning": "ok"}'

        _make_reformulator(capturing_classifier).reformulate("what is the buffett indicator?")

        assert received["question"] == "what is the buffett indicator?"
        assert received["market"] == "US"
        assert received["period_start"] == date(2026, 5, 19)
        assert received["period_end"] == date(2026, 5, 22)

    def test_two_queries_returned(self):
        def multi_query(question, market, period_start, period_end):
            return '{"queries": ["Buffett Indicator 2026", "total market cap GDP ratio US"], "reasoning": "two angles"}'

        result = _make_reformulator(multi_query).reformulate("buffett indicator")

        assert len(result.queries) == 2
        assert result.used_fallback is False

    def test_reformulate_ignores_ticker_and_company_name(self):
        """reformulate accepts ticker/company_name kwargs (for retrieve_for_analyze compat) but ignores them."""
        received = {}

        def capturing_classifier(question, market, period_start, period_end):
            received["question"] = question
            received["market"] = market
            return '{"queries": ["test"], "reasoning": "ok"}'

        _make_reformulator(capturing_classifier).reformulate(
            "test question",
            ticker="AAPL",
            company_name="Apple Inc.",
        )

        assert received["question"] == "test question"
        assert received["market"] == "US"


class TestReformulateFailSafe:
    def test_classifier_throws_returns_fallback(self):
        def broken(question, market, period_start, period_end):
            raise RuntimeError("LLM down")

        result = _make_reformulator(broken).reformulate("what's the buffett indicator?")

        assert result.used_fallback is True
        assert len(result.queries) == 1
        assert result.queries[0] == "what's the buffett indicator? US"

    def test_classifier_returns_garbage_returns_fallback(self):
        def garbage(question, market, period_start, period_end):
            return "not json at all, just text"

        result = RecapQueryReformulator(
            market="VN",
            period_start=date(2026, 5, 19),
            period_end=date(2026, 5, 22),
            classifier=garbage,
        ).reformulate("market valuation")

        assert result.used_fallback is True
        assert result.queries[0] == "market valuation VN"

    def test_classifier_returns_empty_queries_returns_fallback(self):
        def empty_queries(question, market, period_start, period_end):
            return '{"queries": [], "reasoning": "nothing"}'

        result = _make_reformulator(empty_queries).reformulate("earnings outlook")

        assert result.used_fallback is True
