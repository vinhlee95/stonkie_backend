import pytest
from pydantic import ValidationError

from services.analyze_retrieval.schemas import AnalyzeRetrievalResult, AnalyzeSource


def test_analyze_source_constructs_with_optional_published_at() -> None:
    source = AnalyzeSource(
        id="s_1",
        url="https://reuters.com/article/x",
        title="Headline",
        publisher="Reuters",
        is_trusted=True,
    )

    assert source.id == "s_1"
    assert source.published_at is None


def test_analyze_retrieval_result_rejects_unknown_market() -> None:
    with pytest.raises(ValidationError):
        AnalyzeRetrievalResult(
            sources=[],
            query="q",
            market="XX",
            request_id="req-1",
        )


def test_analyze_source_missing_required_field_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        AnalyzeSource(
            id="s_1",
            url="https://reuters.com/article/x",
            title="Headline",
            is_trusted=True,
        )


def test_analyze_retrieval_result_constructs_with_global_market() -> None:
    result = AnalyzeRetrievalResult(
        sources=[],
        query="q",
        market="GLOBAL",
        request_id="req-1",
    )
    assert result.market == "GLOBAL"


def test_analyze_retrieval_result_reformulated_queries_defaults_to_none() -> None:
    result = AnalyzeRetrievalResult(
        sources=[],
        query="q",
        market="GLOBAL",
        request_id="req-1",
    )
    assert result.reformulated_queries is None


def test_analyze_retrieval_result_accepts_reformulated_queries() -> None:
    result = AnalyzeRetrievalResult(
        sources=[],
        query="q",
        market="GLOBAL",
        request_id="req-1",
        reformulated_queries=["Apple Mac market share 2026 IDC", "Mac shipments by region"],
    )
    assert result.reformulated_queries == ["Apple Mac market share 2026 IDC", "Mac shipments by region"]
