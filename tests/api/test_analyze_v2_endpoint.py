import json
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

import main as main_module
from services.analyze_retrieval.schemas import BraveRetrievalError
from services.semantic_analysis_cache import SEMANTIC_CACHE_ENABLED_ENV


def _parse_sse(raw: bytes) -> list[dict]:
    events: list[dict] = []
    for block in raw.decode("utf-8").strip().split("\n\n"):
        if block.strip():
            events.append(json.loads(block))
    return events


def test_v2_analyze_preserves_cookie_and_conversation_stream_contract():
    async def fake_analyze(*_args, **_kwargs):
        yield {"type": "answer", "body": "live answer"}
        yield {"type": "model_used", "body": "test-model"}

    with (
        patch("services.analyze_v2_stream.append_user_message"),
        patch("services.analyze_v2_stream.append_assistant_message"),
        patch("services.analyze_v2_stream.get_conversation_history_for_prompt", return_value=[]),
        patch("services.analyze_v2_stream.get_etf_by_ticker", return_value=None),
        patch("api.analyze_v2.financial_analyzer_v2.analyze_question", side_effect=fake_analyze),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.lookup_hit", return_value=None),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.schedule_background_store"),
    ):
        with TestClient(main_module.app) as client:
            with client.stream(
                "POST",
                "/api/v2/companies/AAPL/analyze",
                json={"question": "What is Apple revenue?", "conversationId": "conv-v2-1"},
            ) as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("text/event-stream")
                assert "anon_user_id=" in response.headers.get("set-cookie", "")
                body = response.read()

    events = _parse_sse(body)
    assert events[0] == {"type": "conversation", "body": {"conversationId": "conv-v2-1"}}
    assert events[1] == {"type": "answer", "body": "live answer"}
    assert events[2] == {"type": "model_used", "body": "test-model"}


def test_v2_analyze_passes_previous_conversation_context_to_financial_analyzer():
    prior_messages = [
        {"role": "user", "content": "What was revenue?"},
        {"role": "assistant", "content": "Revenue was up."},
    ]
    captured_kwargs = {}

    async def fake_analyze(*_args, **kwargs):
        captured_kwargs.update(kwargs)
        yield {"type": "answer", "body": "follow-up answer"}
        yield {"type": "model_used", "body": "test-model"}

    with (
        patch("services.analyze_v2_stream.append_user_message") as append_user,
        patch("services.analyze_v2_stream.append_assistant_message") as append_assistant,
        patch(
            "services.analyze_v2_stream.get_conversation_history_for_prompt",
            return_value=prior_messages,
        ) as history,
        patch("services.analyze_v2_stream.get_etf_by_ticker", return_value=None),
        patch("services.analyze_v2_stream.generate_conversation_id") as generate_conversation_id,
        patch("api.analyze_v2.financial_analyzer_v2.analyze_question", side_effect=fake_analyze),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.lookup_hit", return_value=None),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.schedule_background_store"),
    ):
        with TestClient(main_module.app) as client:
            with client.stream(
                "POST",
                "/api/v2/companies/AAPL/analyze",
                cookies={"anon_user_id": "anon-existing"},
                json={"question": "What about margins?", "conversationId": "conv-existing"},
            ) as response:
                assert response.status_code == 200
                assert "set-cookie" not in response.headers
                response.read()

    history.assert_called_once_with("anon-existing", "AAPL", "conv-existing")
    append_user.assert_called_once_with("anon-existing", "AAPL", "conv-existing", "What about margins?")
    append_assistant.assert_called_once_with("anon-existing", "AAPL", "conv-existing", "follow-up answer")
    generate_conversation_id.assert_not_called()
    assert captured_kwargs["conversation_messages"] == prior_messages
    assert captured_kwargs["conversation_id"] == "conv-existing"
    assert captured_kwargs["anon_user_id"] == "anon-existing"


def test_v2_analyze_streams_etf_analyzer_events_under_etf_conversation_namespace(monkeypatch):
    monkeypatch.setenv(SEMANTIC_CACHE_ENABLED_ENV, "true")
    captured: dict = {}

    async def stock_analyzer_should_not_run(*_args, **_kwargs):
        raise AssertionError("FinancialAnalyzerV2 must not run for ETF tickers")
        yield  # pragma: no cover

    async def fake_etf_analyze(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        yield {"type": "thinking_status", "body": "Searching for the latest SPY data..."}
        yield {"type": "answer", "body": "SPY tracks the S&P 500.\n\n```html\n<div>chart</div>\n```\n"}
        yield {"type": "sources", "body": [{"source_id": "s_1", "url": "https://www.justetf.com/x"}]}
        yield {"type": "model_used", "body": "test-model"}
        yield {"type": "related_question", "body": "What is SPY's TER?"}

    prior = [{"role": "user", "content": "What is SPY?"}, {"role": "assistant", "content": "An S&P 500 ETF."}]
    with (
        patch("services.analyze_v2_stream.append_user_message") as append_user,
        patch("services.analyze_v2_stream.append_assistant_message") as append_assistant,
        patch("services.analyze_v2_stream.get_conversation_history_for_prompt", return_value=prior) as history,
        patch("services.analyze_v2_stream.get_etf_by_ticker", return_value={"ticker": "SPY"}),
        patch("api.analyze_v2.financial_analyzer_v2.analyze_question", side_effect=stock_analyzer_should_not_run),
        patch("api.analyze_v2.etf_analyzer_v2.analyze_question", side_effect=fake_etf_analyze),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.lookup_hit", return_value=None) as lookup,
        patch("services.analyze_v2_stream.SemanticAnalysisCache.schedule_background_store") as schedule_store,
    ):
        with TestClient(main_module.app) as client:
            with client.stream(
                "POST",
                "/api/v2/companies/spy/analyze",
                cookies={"anon_user_id": "anon-etf"},
                json={"question": "What is its TER?", "conversationId": "conv-etf"},
            ) as response:
                assert response.status_code == 200
                body = response.read()

    events = _parse_sse(body)
    types = [event["type"] for event in events]
    assert events[0] == {"type": "conversation", "body": {"conversationId": "conv-etf"}}
    assert "error" not in types
    assert events[1] == {"type": "thinking_status", "body": "Searching for the latest SPY data..."}
    assert {"type": "answer", "body": "SPY tracks the S&P 500.\n\n"} in events
    assert "answer_visual_done" in types  # same visual splitter as stocks
    assert types[-3:] == ["sources", "model_used", "related_question"]

    assert captured["args"][:2] == ("SPY", "What is its TER?")
    assert captured["kwargs"]["conversation_messages"] == prior
    assert captured["kwargs"]["conversation_id"] == "conv-etf"
    history.assert_called_once_with("anon-etf", "etf_SPY", "conv-etf")
    append_user.assert_called_once_with("anon-etf", "etf_SPY", "conv-etf", "What is its TER?")
    append_assistant.assert_called_once_with(
        "anon-etf", "etf_SPY", "conv-etf", "SPY tracks the S&P 500.\n\n```html\n<div>chart</div>\n```\n"
    )
    lookup.assert_called_once_with("v2:SPY", "What is its TER?")
    kwargs = schedule_store.call_args.kwargs
    assert kwargs["cache_ticker"] == "v2:SPY"
    assert kwargs["last_sources_payload"] == [{"source_id": "s_1", "url": "https://www.justetf.com/x"}]
    assert kwargs["last_model_used"] == "test-model"
    assert kwargs["related_questions"] == ["What is SPY's TER?"]


def test_v2_analyze_etf_cache_hit_replays_without_running_the_etf_analyzer(monkeypatch):
    monkeypatch.setenv(SEMANTIC_CACHE_ENABLED_ENV, "true")
    entry = MagicMock()
    entry.answer_text = "cached ETF answer"
    entry.sources = None
    entry.model_used = "model-x"
    entry.related_questions = ["one?", "two?", "three?"]

    async def analyzer_should_not_run(*_args, **_kwargs):
        raise AssertionError("analyzer should not run on v2 ETF cache hit")
        yield  # pragma: no cover

    with (
        patch("services.analyze_v2_stream.append_user_message"),
        patch("services.analyze_v2_stream.append_assistant_message") as append_assistant,
        patch("services.analyze_v2_stream.get_conversation_history_for_prompt", return_value=[]),
        patch("services.analyze_v2_stream.get_etf_by_ticker", return_value={"ticker": "SPY"}),
        patch("api.analyze_v2.financial_analyzer_v2.analyze_question", side_effect=analyzer_should_not_run),
        patch("api.analyze_v2.etf_analyzer_v2.analyze_question", side_effect=analyzer_should_not_run),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.lookup_hit", return_value=entry) as lookup,
    ):
        with TestClient(main_module.app) as client:
            with client.stream(
                "POST",
                "/api/v2/companies/SPY/analyze",
                cookies={"anon_user_id": "anon-etf"},
                json={"question": "What is the TER?", "conversationId": "conv-etf-cache"},
            ) as response:
                assert response.status_code == 200
                body = response.read()

    lookup.assert_called_once_with("v2:SPY", "What is the TER?")
    append_assistant.assert_called_once_with("anon-etf", "etf_SPY", "conv-etf-cache", "cached ETF answer")
    events = _parse_sse(body)
    assert events[0] == {"type": "conversation", "body": {"conversationId": "conv-etf-cache"}}
    assert "cache_meta" in [event["type"] for event in events]


def test_v2_analyze_etf_brave_retrieval_error_maps_to_retrieval_failed():
    async def fake_etf_analyze(*_args, **_kwargs):
        raise BraveRetrievalError("brave is down")
        yield  # pragma: no cover

    with (
        patch("services.analyze_v2_stream.append_user_message"),
        patch("services.analyze_v2_stream.append_assistant_message") as append_assistant,
        patch("services.analyze_v2_stream.get_conversation_history_for_prompt", return_value=[]),
        patch("services.analyze_v2_stream.get_etf_by_ticker", return_value={"ticker": "SPY"}),
        patch("api.analyze_v2.etf_analyzer_v2.analyze_question", side_effect=fake_etf_analyze),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.lookup_hit", return_value=None),
    ):
        with TestClient(main_module.app) as client:
            with client.stream(
                "POST",
                "/api/v2/companies/SPY/analyze",
                json={"question": "Latest SPY flows?", "conversationId": "conv-etf-brave"},
            ) as response:
                assert response.status_code == 200
                body = response.read()

    events = _parse_sse(body)
    assert events[1]["type"] == "error"
    assert events[1]["code"] == "retrieval_failed"
    append_assistant.assert_not_called()


def test_v2_analyze_stock_ticker_does_not_run_the_etf_analyzer():
    async def fake_analyze(*_args, **_kwargs):
        yield {"type": "answer", "body": "stock answer"}

    async def etf_should_not_run(*_args, **_kwargs):
        raise AssertionError("ETFAnalyzerV2 must not run for stock tickers")
        yield  # pragma: no cover

    with (
        patch("services.analyze_v2_stream.append_user_message"),
        patch("services.analyze_v2_stream.append_assistant_message") as append_assistant,
        patch("services.analyze_v2_stream.get_conversation_history_for_prompt", return_value=[]),
        patch("services.analyze_v2_stream.get_etf_by_ticker", return_value=None),
        patch("api.analyze_v2.financial_analyzer_v2.analyze_question", side_effect=fake_analyze),
        patch("api.analyze_v2.etf_analyzer_v2.analyze_question", side_effect=etf_should_not_run),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.lookup_hit", return_value=None),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.schedule_background_store"),
    ):
        with TestClient(main_module.app) as client:
            with client.stream(
                "POST",
                "/api/v2/companies/AAPL/analyze",
                cookies={"anon_user_id": "anon-stock"},
                json={"question": "Revenue?", "conversationId": "conv-stock"},
            ) as response:
                assert response.status_code == 200
                response.read()

    append_assistant.assert_called_once_with("anon-stock", "AAPL", "conv-stock", "stock answer")


def test_v2_analyze_translates_brave_retrieval_error_to_sse_error():
    async def fake_analyze(*_args, **_kwargs):
        raise BraveRetrievalError("brave is down")
        yield  # pragma: no cover

    with (
        patch("services.analyze_v2_stream.append_user_message"),
        patch("services.analyze_v2_stream.append_assistant_message"),
        patch("services.analyze_v2_stream.get_conversation_history_for_prompt", return_value=[]),
        patch("services.analyze_v2_stream.get_etf_by_ticker", return_value=None),
        patch("api.analyze_v2.financial_analyzer_v2.analyze_question", side_effect=fake_analyze),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.lookup_hit", return_value=None),
    ):
        with TestClient(main_module.app) as client:
            with client.stream(
                "POST",
                "/api/v2/companies/AAPL/analyze",
                json={"question": "Latest Apple news?", "conversationId": "conv-brave-error"},
            ) as response:
                assert response.status_code == 200
                body = response.read()

    events = _parse_sse(body)
    assert events[0] == {"type": "conversation", "body": {"conversationId": "conv-brave-error"}}
    assert events[1]["type"] == "error"
    assert events[1]["code"] == "retrieval_failed"


def test_v2_analyze_cache_hit_uses_v2_ticker_namespace_and_skips_analyzer(monkeypatch):
    monkeypatch.setenv(SEMANTIC_CACHE_ENABLED_ENV, "true")
    entry = MagicMock()
    entry.answer_text = "cached v2 answer"
    entry.sources = None
    entry.model_used = "model-x"
    entry.related_questions = ["one?", "two?", "three?"]

    async def analyzer_should_not_run(*_args, **_kwargs):
        raise AssertionError("analyzer should not run on v2 cache hit")
        yield  # pragma: no cover

    with (
        patch("services.analyze_v2_stream.append_user_message"),
        patch("services.analyze_v2_stream.append_assistant_message"),
        patch("services.analyze_v2_stream.get_conversation_history_for_prompt", return_value=[]),
        patch("services.analyze_v2_stream.get_etf_by_ticker", return_value=None),
        patch("api.analyze_v2.financial_analyzer_v2.analyze_question", side_effect=analyzer_should_not_run),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.lookup_hit", return_value=entry) as lookup,
    ):
        with TestClient(main_module.app) as client:
            with client.stream(
                "POST",
                "/api/v2/companies/AAPL/analyze",
                json={"question": "What is margin?", "conversationId": "conv-cache-hit"},
            ) as response:
                assert response.status_code == 200
                body = response.read()

    lookup.assert_called_once_with("v2:AAPL", "What is margin?")
    events = _parse_sse(body)
    assert events[0] == {"type": "conversation", "body": {"conversationId": "conv-cache-hit"}}
    assert "cache_meta" in [event["type"] for event in events]


def test_v2_analyze_cache_miss_schedules_store_with_v2_namespace_and_30_minute_ttl(monkeypatch):
    monkeypatch.setenv(SEMANTIC_CACHE_ENABLED_ENV, "true")

    async def fake_analyze(*_args, **_kwargs):
        yield {"type": "answer", "body": "live "}
        yield {"type": "answer", "body": "v2 answer"}
        yield {"type": "sources", "body": [{"source_id": "s_1", "url": "https://example.com/1"}]}
        yield {"type": "model_used", "body": "test-model"}
        yield {"type": "related_question", "body": "Related one?"}

    with (
        patch("services.analyze_v2_stream.append_user_message"),
        patch("services.analyze_v2_stream.append_assistant_message"),
        patch("services.analyze_v2_stream.get_conversation_history_for_prompt", return_value=[]),
        patch("services.analyze_v2_stream.get_etf_by_ticker", return_value=None),
        patch("api.analyze_v2.financial_analyzer_v2.analyze_question", side_effect=fake_analyze),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.lookup_hit", return_value=None),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.schedule_background_store") as schedule_store,
    ):
        with TestClient(main_module.app) as client:
            with client.stream(
                "POST",
                "/api/v2/companies/AAPL/analyze",
                json={"question": "What is margin?", "conversationId": "conv-cache-miss"},
            ) as response:
                assert response.status_code == 200
                response.read()

    schedule_store.assert_called_once()
    kwargs = schedule_store.call_args.kwargs
    assert kwargs["cache_ticker"] == "v2:AAPL"
    assert kwargs["assistant_full_text"] == "live v2 answer"
    assert kwargs["last_model_used"] == "test-model"
    assert kwargs["related_questions"] == ["Related one?"]
    assert kwargs["ttl_seconds"] == 30 * 60
    assert kwargs["last_sources_payload"] == [{"source_id": "s_1", "url": "https://example.com/1"}]
    assert kwargs["use_semantic_cache"] is True


def test_v2_analyze_debug_prompt_context_event_is_streamed_when_requested():
    async def fake_analyze(*_args, **kwargs):
        assert kwargs["debug_prompt_context"] is True
        yield {"type": "debug_prompt_context", "body": {"handler": "company_general_v2", "prompt": "P"}}
        yield {"type": "answer", "body": "live answer"}
        yield {"type": "model_used", "body": "test-model"}

    with (
        patch("services.analyze_v2_stream.append_user_message"),
        patch("services.analyze_v2_stream.append_assistant_message"),
        patch("services.analyze_v2_stream.get_conversation_history_for_prompt", return_value=[]),
        patch("services.analyze_v2_stream.get_etf_by_ticker", return_value=None),
        patch("api.analyze_v2.financial_analyzer_v2.analyze_question", side_effect=fake_analyze),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.lookup_hit", return_value=None),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.schedule_background_store"),
    ):
        with TestClient(main_module.app) as client:
            with client.stream(
                "POST",
                "/api/v2/companies/AAPL/analyze",
                json={"question": "What is Apple revenue?", "debugPromptContext": True},
            ) as response:
                assert response.status_code == 200
                body = response.read()

    events = _parse_sse(body)
    assert events[1] == {"type": "debug_prompt_context", "body": {"handler": "company_general_v2", "prompt": "P"}}


def test_v2_analyze_disable_cache_bypasses_lookup_and_store():
    async def fake_analyze(*_args, **kwargs):
        yield {"type": "answer", "body": "fresh answer"}
        yield {"type": "model_used", "body": "test-model"}

    with (
        patch("services.analyze_v2_stream.append_user_message"),
        patch("services.analyze_v2_stream.append_assistant_message"),
        patch("services.analyze_v2_stream.get_conversation_history_for_prompt", return_value=[]),
        patch("services.analyze_v2_stream.get_etf_by_ticker", return_value=None),
        patch("api.analyze_v2.financial_analyzer_v2.analyze_question", side_effect=fake_analyze),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.lookup_hit") as lookup,
        patch("services.analyze_v2_stream.SemanticAnalysisCache.schedule_background_store") as schedule_store,
    ):
        with TestClient(main_module.app) as client:
            with client.stream(
                "POST",
                "/api/v2/companies/AAPL/analyze",
                json={"question": "What is Apple revenue?", "disableCache": True},
            ) as response:
                assert response.status_code == 200
                response.read()

    lookup.assert_not_called()
    schedule_store.assert_called_once()
    assert schedule_store.call_args.kwargs["use_semantic_cache"] is False


def test_v2_analyze_semantic_cache_off_by_default_skips_lookup_and_store(monkeypatch):
    monkeypatch.delenv(SEMANTIC_CACHE_ENABLED_ENV, raising=False)

    async def fake_analyze(*_args, **kwargs):
        yield {"type": "answer", "body": "fresh answer"}
        yield {"type": "model_used", "body": "test-model"}

    with (
        patch("services.analyze_v2_stream.append_user_message"),
        patch("services.analyze_v2_stream.append_assistant_message"),
        patch("services.analyze_v2_stream.get_conversation_history_for_prompt", return_value=[]),
        patch("services.analyze_v2_stream.get_etf_by_ticker", return_value=None),
        patch("api.analyze_v2.financial_analyzer_v2.analyze_question", side_effect=fake_analyze),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.lookup_hit") as lookup,
        patch("services.analyze_v2_stream.SemanticAnalysisCache.schedule_background_store") as schedule_store,
    ):
        with TestClient(main_module.app) as client:
            with client.stream(
                "POST",
                "/api/v2/companies/AAPL/analyze",
                json={"question": "What is Apple revenue?"},
            ) as response:
                assert response.status_code == 200
                response.read()

    lookup.assert_not_called()
    assert schedule_store.call_args.kwargs["use_semantic_cache"] is False


def test_v2_live_stream_splits_visual_answer_blocks():
    async def fake_analyze(*_args, **_kwargs):
        yield {"type": "answer", "body": "Intro\n\n```html\n<div>chart</div>\n```\nDone"}
        yield {"type": "model_used", "body": "test-model"}

    with (
        patch("services.analyze_v2_stream.append_user_message"),
        patch("services.analyze_v2_stream.append_assistant_message"),
        patch("services.analyze_v2_stream.get_conversation_history_for_prompt", return_value=[]),
        patch("services.analyze_v2_stream.get_etf_by_ticker", return_value=None),
        patch("api.analyze_v2.financial_analyzer_v2.analyze_question", side_effect=fake_analyze),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.lookup_hit", return_value=None),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.schedule_background_store"),
    ):
        with TestClient(main_module.app) as client:
            with client.stream(
                "POST",
                "/api/v2/companies/AAPL/analyze",
                json={"question": "Show visual", "conversationId": "conv-visual-live"},
            ) as response:
                assert response.status_code == 200
                body = response.read()

    types = [event["type"] for event in _parse_sse(body)]
    assert "answer_visual_start" in types
    assert "answer_visual_delta" in types
    assert "answer_visual_done" in types


def test_v2_cache_replay_splits_visual_answer_blocks(monkeypatch):
    monkeypatch.setenv(SEMANTIC_CACHE_ENABLED_ENV, "true")
    entry = MagicMock()
    entry.answer_text = "Intro\n\n```html\n<div>cached chart</div>\n```\n"
    entry.sources = None
    entry.model_used = "model-x"
    entry.related_questions = ["one?", "two?", "three?"]

    async def analyzer_should_not_run(*_args, **_kwargs):
        raise AssertionError("analyzer should not run on cache hit")
        yield  # pragma: no cover

    with (
        patch("services.analyze_v2_stream.append_user_message"),
        patch("services.analyze_v2_stream.append_assistant_message"),
        patch("services.analyze_v2_stream.get_conversation_history_for_prompt", return_value=[]),
        patch("services.analyze_v2_stream.get_etf_by_ticker", return_value=None),
        patch("api.analyze_v2.financial_analyzer_v2.analyze_question", side_effect=analyzer_should_not_run),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.lookup_hit", return_value=entry),
    ):
        with TestClient(main_module.app) as client:
            with client.stream(
                "POST",
                "/api/v2/companies/AAPL/analyze",
                json={"question": "Show cached visual", "conversationId": "conv-visual-cache"},
            ) as response:
                assert response.status_code == 200
                body = response.read()

    types = [event["type"] for event in _parse_sse(body)]
    assert "answer_visual_start" in types
    assert "answer_visual_delta" in types
    assert "answer_visual_done" in types
    assert "cache_meta" in types


def test_v2_analyze_does_not_strip_whitespace_inside_visual_block():
    """Citation cleaner must not run on text inside ```html / ```svg fenced blocks.

    Reproduces the META cache id=123 bug: chunk-boundary leading-space loss inside
    a visual block fused `var canvas` into `varcanvas`, breaking Chart.js.
    """

    async def fake_analyze(*_args, **_kwargs):
        yield {"type": "answer", "body": "Intro.\n\n```html\n<script>var"}
        yield {"type": "answer", "body": " canvas = document.getElementById('c');"}
        yield {"type": "answer", "body": "</script>\n```\n\nOutro."}

    with (
        patch("services.analyze_v2_stream.append_user_message"),
        patch("services.analyze_v2_stream.append_assistant_message"),
        patch("services.analyze_v2_stream.get_conversation_history_for_prompt", return_value=[]),
        patch("services.analyze_v2_stream.get_etf_by_ticker", return_value=None),
        patch("api.analyze_v2.financial_analyzer_v2.analyze_question", side_effect=fake_analyze),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.lookup_hit", return_value=None),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.schedule_background_store"),
    ):
        with TestClient(main_module.app) as client:
            with client.stream(
                "POST",
                "/api/v2/companies/AAPL/analyze",
                json={"question": "show chart", "conversationId": "conv-vis-1"},
            ) as response:
                assert response.status_code == 200
                body = response.read()

    events = _parse_sse(body)
    done = next(e for e in events if e["type"] == "answer_visual_done")
    assert "var canvas" in done["body"]["content"]
    assert "varcanvas" not in done["body"]["content"]


def test_v2_analyze_does_not_strip_bracket_indexing_inside_visual_block():
    """Citation marker stripping (`[N]`) must not eat array indexing inside visual blocks."""

    async def fake_analyze(*_args, **_kwargs):
        yield {"type": "answer", "body": "Intro.\n\n```html\n<script>var x = arr[1] + arr[2];</script>\n```\n"}

    with (
        patch("services.analyze_v2_stream.append_user_message"),
        patch("services.analyze_v2_stream.append_assistant_message"),
        patch("services.analyze_v2_stream.get_conversation_history_for_prompt", return_value=[]),
        patch("services.analyze_v2_stream.get_etf_by_ticker", return_value=None),
        patch("api.analyze_v2.financial_analyzer_v2.analyze_question", side_effect=fake_analyze),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.lookup_hit", return_value=None),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.schedule_background_store"),
    ):
        with TestClient(main_module.app) as client:
            with client.stream(
                "POST",
                "/api/v2/companies/AAPL/analyze",
                json={"question": "indexing", "conversationId": "conv-vis-2"},
            ) as response:
                assert response.status_code == 200
                body = response.read()

    events = _parse_sse(body)
    done = next(e for e in events if e["type"] == "answer_visual_done")
    assert "arr[1]" in done["body"]["content"]
    assert "arr[2]" in done["body"]["content"]


def test_v2_analyze_does_not_strip_grouped_citation_pattern_inside_visual_block():
    """A JS array literal `[26, 13, 24, 14]` matches the citation pattern. Must survive."""

    async def fake_analyze(*_args, **_kwargs):
        yield {"type": "answer", "body": "Intro [1].\n\n```html\n<script>var data = [26, 13, 24, 14];</script>\n```\n"}

    with (
        patch("services.analyze_v2_stream.append_user_message"),
        patch("services.analyze_v2_stream.append_assistant_message"),
        patch("services.analyze_v2_stream.get_conversation_history_for_prompt", return_value=[]),
        patch("services.analyze_v2_stream.get_etf_by_ticker", return_value=None),
        patch("api.analyze_v2.financial_analyzer_v2.analyze_question", side_effect=fake_analyze),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.lookup_hit", return_value=None),
        patch("services.analyze_v2_stream.SemanticAnalysisCache.schedule_background_store"),
    ):
        with TestClient(main_module.app) as client:
            with client.stream(
                "POST",
                "/api/v2/companies/META/analyze",
                json={"question": "compare ads", "conversationId": "conv-vis-3"},
            ) as response:
                assert response.status_code == 200
                body = response.read()

    events = _parse_sse(body)
    done = next(e for e in events if e["type"] == "answer_visual_done")
    assert "[26, 13, 24, 14]" in done["body"]["content"]
