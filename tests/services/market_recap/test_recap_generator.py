from datetime import UTC, date, datetime

from services.market_recap.schemas import Candidate, RetrievalResult, RetrievalStats


class FakeAgent:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []
        self.model_name = "fake-model"

    def generate_content(self, prompt: str, use_google_search: bool = False):
        self.calls.append({"prompt": prompt, "use_google_search": use_google_search})
        return self.chunks


def _candidate(title: str, url: str, raw_content: str) -> Candidate:
    return Candidate(
        title=title,
        url=url,
        snippet="snippet",
        published_date=datetime(2026, 4, 24, 12, 0, tzinfo=UTC),
        raw_content=raw_content,
        score=0.9,
        provider="tavily",
    )


def _retrieval() -> RetrievalResult:
    return RetrievalResult(
        candidates=[
            _candidate("Reuters A", "https://www.reuters.com/markets/a", "Body A"),
            _candidate("AP B", "https://apnews.com/article/b", "Body B"),
        ],
        stats=RetrievalStats(
            queries_total=1,
            results_total=2,
            deduped=2,
            with_raw_content=2,
            allowlisted=2,
            ranked_top_k=2,
        ),
    )


def test_prompt_contains_period_and_indexed_corpus():
    from services.market_recap.recap_generator import GeneratorError, generate_recap

    agent = FakeAgent(chunks=['[RECAP_JSON]{"summary":"s","bullets":[]}[/RECAP_JSON]'])
    try:
        generate_recap(
            _retrieval(),
            period_start=date(2026, 4, 20),
            period_end=date(2026, 4, 24),
            agent=agent,
        )
    except GeneratorError:
        pass

    assert len(agent.calls) == 1
    prompt = agent.calls[0]["prompt"]
    assert "2026-04-20" in prompt
    assert "2026-04-24" in prompt
    assert "Source [0]" in prompt
    assert "Source [1]" in prompt
    assert "https://www.reuters.com/markets/a" in prompt
    assert "Body B" in prompt
    assert agent.calls[0]["use_google_search"] is False


def test_extracts_recap_json_between_markers():
    from services.market_recap.recap_generator import generate_recap

    agent = FakeAgent(
        chunks=[
            'preamble [RECAP_JSON]{"summary":"Weekly summary","bullets":[{"text":"b1","source_indices":[0]}]}[/RECAP_JSON] trailing'
        ]
    )
    result = generate_recap(
        _retrieval(),
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        agent=agent,
    )
    assert result.payload.summary == "Weekly summary"


def test_resolves_source_indices_to_canonical_sources():
    from services.market_recap.recap_generator import generate_recap
    from services.market_recap.url_utils import source_id_for

    agent = FakeAgent(
        chunks=[
            '[RECAP_JSON]{"summary":"s","bullets":[{"text":"b1","source_indices":[0]},{"text":"b2","source_indices":[1]}]}[/RECAP_JSON]'
        ]
    )
    retrieval = _retrieval()
    result = generate_recap(
        retrieval,
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        agent=agent,
    )

    source_ids = {source.id for source in result.payload.sources}
    assert source_ids == {source_id_for(c.url) for c in retrieval.candidates}
    assert result.payload.bullets[0].citations[0].source_id in source_ids
    assert result.payload.bullets[1].citations[0].source_id in source_ids


def test_dedupes_sources_when_indices_repeat_across_bullets():
    from services.market_recap.recap_generator import generate_recap

    agent = FakeAgent(
        chunks=[
            '[RECAP_JSON]{"summary":"s","bullets":[{"text":"b1","source_indices":[0]},{"text":"b2","source_indices":[0]}]}[/RECAP_JSON]'
        ]
    )
    result = generate_recap(
        _retrieval(),
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        agent=agent,
    )

    assert len(result.payload.sources) == 1
    assert result.payload.bullets[0].citations[0].source_id == result.payload.bullets[1].citations[0].source_id


def test_filters_dict_chunks_from_stream():
    from services.market_recap.recap_generator import generate_recap

    agent = FakeAgent(
        chunks=[
            "prefix ",
            {"type": "url_citation", "url": "https://www.reuters.com"},
            '[RECAP_JSON]{"summary":"s","bullets":[{"text":"b1","source_indices":[0]}]}[/RECAP_JSON]',
        ]
    )
    result = generate_recap(
        _retrieval(),
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        agent=agent,
    )
    assert result.payload.summary == "s"


def test_missing_markers_raises_generator_error():
    import pytest

    from services.market_recap.recap_generator import GeneratorError, generate_recap

    agent = FakeAgent(chunks=['{"summary":"s","bullets":[]}'])
    with pytest.raises(GeneratorError):
        generate_recap(
            _retrieval(),
            period_start=date(2026, 4, 20),
            period_end=date(2026, 4, 24),
            agent=agent,
        )


def test_out_of_range_source_index_raises_generator_error():
    import pytest

    from services.market_recap.recap_generator import GeneratorError, generate_recap

    agent = FakeAgent(chunks=['[RECAP_JSON]{"summary":"s","bullets":[{"text":"b","source_indices":[7]}]}[/RECAP_JSON]'])
    with pytest.raises(GeneratorError):
        generate_recap(
            _retrieval(),
            period_start=date(2026, 4, 20),
            period_end=date(2026, 4, 24),
            agent=agent,
        )


def test_returns_model_name_for_audit():
    from services.market_recap.recap_generator import generate_recap

    agent = FakeAgent(chunks=['[RECAP_JSON]{"summary":"s","bullets":[{"text":"b","source_indices":[0]}]}[/RECAP_JSON]'])
    agent.model_name = "openrouter/test-model"
    result = generate_recap(
        _retrieval(),
        period_start=date(2026, 4, 20),
        period_end=date(2026, 4, 24),
        agent=agent,
    )
    assert result.model == "openrouter/test-model"
