import pytest

from services.analyze_retrieval.query_reformulator import QueryReformulator, ReformulationResult


class TestParseQueries:
    def test_valid_json_single_query(self):
        raw = (
            '{"queries": ["Apple Mac market share by region 2026 IDC Canalys"], "reasoning": "Added data source hints"}'
        )
        parsed = QueryReformulator._parse_queries(raw)

        assert parsed["queries"] == ["Apple Mac market share by region 2026 IDC Canalys"]
        assert parsed["reasoning"] == "Added data source hints"

    def test_valid_json_two_queries(self):
        raw = '{"queries": ["Apple Mac shipments by region 2026", "Mac market share Americas Europe Asia IDC"], "reasoning": "Split by angle"}'
        parsed = QueryReformulator._parse_queries(raw)

        assert len(parsed["queries"]) == 2
        assert parsed["queries"][0] == "Apple Mac shipments by region 2026"
        assert parsed["queries"][1] == "Mac market share Americas Europe Asia IDC"

    def test_json_wrapped_in_markdown_fences(self):
        raw = 'here is the result ```json\n{"queries": ["AAPL revenue breakdown Q2 2026"], "reasoning": "focused"}\n```'
        parsed = QueryReformulator._parse_queries(raw)

        assert parsed["queries"] == ["AAPL revenue breakdown Q2 2026"]

    def test_missing_queries_key_raises(self):
        raw = '{"search_terms": ["something"], "reasoning": "bad"}'
        with pytest.raises(ValueError, match="queries"):
            QueryReformulator._parse_queries(raw)

    def test_empty_queries_list_raises(self):
        raw = '{"queries": [], "reasoning": "empty"}'
        with pytest.raises(ValueError, match="empty"):
            QueryReformulator._parse_queries(raw)

    def test_non_string_query_items_raises(self):
        raw = '{"queries": [123, true], "reasoning": "wrong types"}'
        with pytest.raises(ValueError, match="string"):
            QueryReformulator._parse_queries(raw)

    def test_no_json_in_output_raises(self):
        raw = "I cannot reformulate this question."
        with pytest.raises(ValueError, match="No JSON"):
            QueryReformulator._parse_queries(raw)

    def test_caps_at_three_queries(self):
        raw = '{"queries": ["q1", "q2", "q3", "q4", "q5"], "reasoning": "many"}'
        parsed = QueryReformulator._parse_queries(raw)

        assert len(parsed["queries"]) == 3


class TestReformulateFailSafe:
    def test_classifier_throws_returns_fallback(self):
        def broken(question: str, ticker: str, company_name: str) -> str:
            raise RuntimeError("LLM down")

        reformulator = QueryReformulator(classifier=broken)
        result = reformulator.reformulate("Mac share by region", ticker="AAPL", company_name="Apple Inc.")

        assert isinstance(result, ReformulationResult)
        assert result.used_fallback is True
        assert len(result.queries) == 1
        assert "Apple Inc." in result.queries[0]
        assert "AAPL" in result.queries[0]

    def test_classifier_returns_garbage_returns_fallback(self):
        def garbage(question: str, ticker: str, company_name: str) -> str:
            return "not json at all, just text"

        reformulator = QueryReformulator(classifier=garbage)
        result = reformulator.reformulate("revenue breakdown", ticker="MSFT", company_name="Microsoft Corp")

        assert result.used_fallback is True
        assert "Microsoft Corp" in result.queries[0]

    def test_classifier_returns_empty_queries_returns_fallback(self):
        def empty_queries(question: str, ticker: str, company_name: str) -> str:
            return '{"queries": [], "reasoning": "nothing"}'

        reformulator = QueryReformulator(classifier=empty_queries)
        result = reformulator.reformulate("earnings", ticker="GOOG", company_name="Alphabet Inc.")

        assert result.used_fallback is True


class TestReformulateHappyPath:
    def test_successful_reformulation(self):
        def good_classifier(question: str, ticker: str, company_name: str) -> str:
            return (
                '{"queries": ["Apple Mac market share by region 2026 IDC Canalys"], "reasoning": "Added source hints"}'
            )

        reformulator = QueryReformulator(classifier=good_classifier)
        result = reformulator.reformulate("Mac share by region", ticker="AAPL", company_name="Apple Inc.")

        assert result.used_fallback is False
        assert result.queries == ["Apple Mac market share by region 2026 IDC Canalys"]
        assert result.reasoning == "Added source hints"

    def test_classifier_receives_correct_args(self):
        received = {}

        def capturing_classifier(question: str, ticker: str, company_name: str) -> str:
            received["question"] = question
            received["ticker"] = ticker
            received["company_name"] = company_name
            return '{"queries": ["test query"], "reasoning": "ok"}'

        reformulator = QueryReformulator(classifier=capturing_classifier)
        reformulator.reformulate("what is revenue", ticker="TSLA", company_name="Tesla Inc.")

        assert received["question"] == "what is revenue"
        assert received["ticker"] == "TSLA"
        assert received["company_name"] == "Tesla Inc."

    def test_two_queries_returned(self):
        def multi_query(question: str, ticker: str, company_name: str) -> str:
            return '{"queries": ["Apple Mac shipments by region", "Mac vs PC market share 2026"], "reasoning": "two angles"}'

        reformulator = QueryReformulator(classifier=multi_query)
        result = reformulator.reformulate("Mac share by region", ticker="AAPL", company_name="Apple Inc.")

        assert len(result.queries) == 2
        assert result.used_fallback is False
