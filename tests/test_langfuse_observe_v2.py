"""Verify Langfuse @observe on FinancialAnalyzerV2.analyze_question captures only question as input, concatenated answer text as output, and TTFT."""

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.financial_analyzer_v2 import FinancialAnalyzerV2, _extract_answer_text
from services.search_decision_engine import SearchDecision


def _make_search_decision():
    return SearchDecision(
        use_google_search=False, reason_code="test", confidence=1.0, decision_model="test", decision_fallback=""
    )


def _make_analyzer(classification="company-general", handler_chunks=None):
    if handler_chunks is None:
        handler_chunks = [
            {"type": "thinking_status", "body": "Analyzing..."},
            {"type": "answer", "body": "Tesla was founded"},
            {"type": "answer", "body": " by Elon Musk."},
            {"type": "sources", "body": [{"url": "https://example.com"}]},
            {"type": "model_used", "body": "gemini-2.5-flash"},
        ]

    async def fake_handler(**kwargs):
        for c in handler_chunks:
            yield c

    classifier = MagicMock()
    classifier.classify_question_type = AsyncMock(return_value=(classification, None))

    search_engine = MagicMock()
    search_engine.decide = AsyncMock(return_value=_make_search_decision())

    company_financial_connector = MagicMock()
    company_financial_connector.get_available_periods.return_value = None
    company_financial_connector.get_available_metrics.return_value = None

    handler_mock = MagicMock()
    handler_mock.handle = fake_handler

    analyzer = FinancialAnalyzerV2(
        classifier=classifier,
        search_decision_engine=search_engine,
        company_financial_connector=company_financial_connector,
        company_general_handler=handler_mock,
        general_finance_handler=handler_mock,
        company_specific_finance_handler=handler_mock,
        comparison_handler=handler_mock,
    )
    return analyzer


# --- _extract_answer_text unit tests ---


def test_extract_answer_text_filters_only_answer_chunks():
    chunks = [
        {"type": "thinking_status", "body": "Analyzing..."},
        {"type": "answer", "body": "Tesla was founded"},
        {"type": "answer", "body": " by Elon Musk."},
        {"type": "sources", "body": [{"url": "https://example.com"}]},
        {"type": "model_used", "body": "gemini-2.5-flash"},
    ]
    assert _extract_answer_text(chunks) == "Tesla was founded by Elon Musk."


def test_extract_answer_text_handles_empty():
    assert _extract_answer_text([]) == ""
    assert _extract_answer_text([{"type": "thinking_status", "body": "..."}]) == ""


def test_extract_answer_text_handles_non_dict_items():
    assert _extract_answer_text(["raw string", 42, {"type": "answer", "body": "ok"}]) == "ok"


# --- Langfuse input ---


@pytest.mark.asyncio
async def test_langfuse_input_set_to_question_only():
    analyzer = _make_analyzer()
    mock_langfuse = MagicMock()

    with (
        patch("services.financial_analyzer_v2.get_langfuse_client", return_value=mock_langfuse),
        patch("langfuse._client.observe.get_client", return_value=None),
    ):
        async for _ in analyzer.analyze_question(
            ticker="TSLA",
            question="Who founded Tesla?",
        ):
            pass

    mock_langfuse.update_current_generation.assert_any_call(input="Who founded Tesla?")


@pytest.mark.asyncio
async def test_langfuse_not_called_when_client_disabled():
    analyzer = _make_analyzer()

    with (
        patch("services.financial_analyzer_v2.get_langfuse_client", return_value=None),
        patch("langfuse._client.observe.get_client", return_value=None),
    ):
        async for _ in analyzer.analyze_question(
            ticker="TSLA",
            question="Who founded Tesla?",
        ):
            pass


# --- TTFT ---


@pytest.mark.asyncio
async def test_langfuse_ttft_recorded_on_first_answer_chunk():
    analyzer = _make_analyzer()
    mock_langfuse = MagicMock()

    with (
        patch("services.financial_analyzer_v2.get_langfuse_client", return_value=mock_langfuse),
        patch("langfuse._client.observe.get_client", return_value=None),
    ):
        async for _ in analyzer.analyze_question(
            ticker="TSLA",
            question="Who founded Tesla?",
        ):
            pass

    ttft_calls = [
        c for c in mock_langfuse.update_current_generation.call_args_list if "completion_start_time" in c.kwargs
    ]
    assert len(ttft_calls) == 1
    assert isinstance(ttft_calls[0].kwargs["completion_start_time"], datetime.datetime)


@pytest.mark.asyncio
async def test_langfuse_ttft_recorded_only_once():
    """Even with multiple answer chunks, completion_start_time is set only on the first."""
    analyzer = _make_analyzer()
    mock_langfuse = MagicMock()

    with (
        patch("services.financial_analyzer_v2.get_langfuse_client", return_value=mock_langfuse),
        patch("langfuse._client.observe.get_client", return_value=None),
    ):
        async for _ in analyzer.analyze_question(
            ticker="TSLA",
            question="Who founded Tesla?",
        ):
            pass

    ttft_calls = [
        c for c in mock_langfuse.update_current_generation.call_args_list if "completion_start_time" in c.kwargs
    ]
    assert len(ttft_calls) == 1


# --- Chunks passthrough ---


@pytest.mark.asyncio
async def test_all_chunks_still_yielded_to_caller():
    analyzer = _make_analyzer()

    with (
        patch("services.financial_analyzer_v2.get_langfuse_client", return_value=None),
        patch("langfuse._client.observe.get_client", return_value=None),
    ):
        chunks = []
        async for chunk in analyzer.analyze_question(
            ticker="TSLA",
            question="Who founded Tesla?",
        ):
            chunks.append(chunk)

    chunk_types = [c.get("type") for c in chunks]
    assert "thinking_status" in chunk_types
    assert "answer" in chunk_types
    assert "sources" in chunk_types
    assert "model_used" in chunk_types
