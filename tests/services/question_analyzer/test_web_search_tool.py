"""WebSearchTool: the answer model's web_search tool over retrieve_for_analyze (fake search, no network)."""

from __future__ import annotations

from ai_models.chat_tool import ChatToolResult
from services.analyze_retrieval.schemas import (
    AnalyzePassage,
    AnalyzeRetrievalResult,
    AnalyzeSource,
    BraveRetrievalError,
)
from services.question_analyzer.web_search_tool import WebSearchTool


def _source(source_id: str, publisher: str = "Reuters", trusted: bool = True) -> AnalyzeSource:
    return AnalyzeSource(
        id=source_id,
        url=f"https://example.com/{source_id}",
        title=f"Title {source_id}",
        publisher=publisher,
        is_trusted=trusted,
    )


def _passage(source_id: str) -> AnalyzePassage:
    return AnalyzePassage(
        source_id=source_id,
        url=f"https://example.com/{source_id}",
        title=f"Title {source_id}",
        publisher="Reuters",
        is_trusted=True,
        passage_index=0,
        content=f"Passage about {source_id}",
    )


def _result(*sources: AnalyzeSource) -> AnalyzeRetrievalResult:
    return AnalyzeRetrievalResult(
        sources=list(sources),
        selected_passages=[_passage(s.id) for s in sources],
        query="q",
        market="GLOBAL",
        request_id="r",
    )


def test_run_formats_passages_and_reports_new_sources():
    queries = []

    def search(query):
        queries.append(query)
        return _result(_source("a"), _source("b", publisher="Blog", trusted=False))

    tool = WebSearchTool(search).chat_tool()
    result = tool.run({"query": "Alphabet Q2 2026 other income"})

    assert queries == ["Alphabet Q2 2026 other income"]
    assert isinstance(result, ChatToolResult)
    assert "Title: Title a" in result.content and "Passage about b" in result.content
    assert result.meta == {"source_count": 2, "publishers": ["Reuters"]}
    assert tool.name == "web_search"
    assert tool.parameters["required"] == ["query"]


def test_run_returns_no_results_on_brave_error():
    def search(query):
        raise BraveRetrievalError("No Brave results available after filtering")

    result = WebSearchTool(search).chat_tool().run({"query": "q"})

    assert result == ChatToolResult("No relevant results.", {"source_count": 0, "publishers": []})


def test_repeat_sources_are_not_counted_again():
    tool = WebSearchTool(lambda query: _result(_source("a"))).chat_tool()

    tool.run({"query": "q1"})
    assert tool.run({"query": "q2"}).meta["source_count"] == 0


def test_sources_already_in_the_prompt_are_not_new():
    web_search = WebSearchTool(lambda query: _result(_source("a"), _source("b")), known_sources=[_source("a")])

    assert web_search.chat_tool().run({"query": "q"}).meta["source_count"] == 1


def test_merged_sources_dedupes_against_initial():
    web_search = WebSearchTool(lambda query: _result(_source("a"), _source("c")))
    web_search.chat_tool().run({"query": "q"})

    sources, passages = web_search.merged_sources([_source("a"), _source("b")], [_passage("a"), _passage("b")])

    assert [s.id for s in sources] == ["a", "b", "c"]
    assert [p.source_id for p in passages] == ["a", "b", "c"]


def test_merged_sources_without_tool_calls_is_initial():
    web_search = WebSearchTool(lambda query: _result())

    sources, _ = web_search.merged_sources([_source("a")], [_passage("a")])

    assert [s.id for s in sources] == ["a"]
