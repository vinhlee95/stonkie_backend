import asyncio

from services.search_decision_engine import SearchDecisionEngine


class TestSearchDecisionEngine:
    def test_parse_decision_valid_json_block(self):
        raw = 'noise ```json {"use_google_search": true, "reason_code": "time_sensitive", "confidence": 0.91} ```'
        parsed = SearchDecisionEngine._parse_decision(raw)

        assert parsed["use_google_search"] is True
        assert parsed["reason_code"] == "time_sensitive"
        assert parsed["confidence"] == 0.91

    def test_decide_fails_safe_on_when_classifier_throws(self):
        def broken_classifier(question: str, ticker: str, is_etf: bool) -> str:
            raise RuntimeError("classifier offline")

        engine = SearchDecisionEngine(classifier=broken_classifier)
        decision = asyncio.run(engine.decide(question="latest NVDA news", ticker="NVDA", is_etf=False))

        assert decision.use_google_search is True
        assert decision.reason_code == "classifier_error"
        assert decision.decision_model == "sonnet-4.6"
        assert decision.decision_fallback == "classifier_fail_safe_on"

    def test_decide_force_reason_overrides_classifier(self):
        def classifier_output(question: str, ticker: str, is_etf: bool) -> str:
            return '{"use_google_search": false, "reason_code": "stable_concept", "confidence": 0.99}'

        engine = SearchDecisionEngine(classifier=classifier_output)
        decision = asyncio.run(
            engine.decide(
                question="analyze this filing",
                ticker="AAPL",
                is_etf=False,
                force_google_search_reason="sec_url",
            )
        )

        assert decision.use_google_search is True
        assert decision.reason_code == "sec_url"
        assert decision.confidence == 1.0
        assert decision.decision_fallback == "none"
