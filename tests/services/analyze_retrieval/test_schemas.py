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
