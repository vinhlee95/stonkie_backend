"""Tests for _process_source_tags and _collect_paragraph_sources stream processors."""

import json

from services.question_analyzer.handlers import _collect_paragraph_sources, _process_source_tags


def _collect(chunks, **kwargs):
    """Run _process_source_tags and return list of events."""
    return list(_process_source_tags(chunks, **kwargs))


def _answers(events):
    return [e["body"] for e in events if e["type"] == "answer"]


def _sources(events):
    return [e["body"] for e in events if e["type"] == "sources"]


class TestBasicPassthrough:
    def test_plain_text_passes_through(self):
        events = _collect(["hello ", "world"])
        assert _answers(events) == ["hello ", "world"]
        assert _sources(events) == []

    def test_empty_chunks_skipped(self):
        events = _collect(["hello", "", "world"])
        assert _answers(events) == ["hello", "world"]


class TestCompleteTagInSingleChunk:
    def test_full_tag_in_one_chunk(self):
        src = json.dumps({"sources": [{"name": "Foo", "url": "https://foo.com"}]})
        chunk = f"Intro text.\n[SOURCES_JSON]{src}[/SOURCES_JSON]\nMore text."
        events = _collect([chunk])
        answers = _answers(events)
        sources = _sources(events)
        assert len(sources) == 1
        assert sources[0][0]["name"] == "Foo"
        assert sources[0][0]["url"] == "https://foo.com"
        # Answer text should not contain the tag
        full_answer = "".join(answers)
        assert "[SOURCES_JSON]" not in full_answer
        assert "Foo" not in full_answer or "https://foo.com" not in full_answer


class TestTagSplitAcrossChunks:
    def test_start_tag_split(self):
        """[SOURCES_JSON] tag split across two chunks."""
        src = json.dumps({"sources": [{"name": "Bar", "url": "https://bar.com"}]})
        chunks = [
            "Some text [SOURCES_",
            f"JSON]{src}[/SOURCES_JSON]\nAfter.",
        ]
        events = _collect(chunks)
        sources = _sources(events)
        assert len(sources) == 1
        assert sources[0][0]["url"] == "https://bar.com"
        full_answer = "".join(_answers(events))
        assert "[SOURCES_JSON]" not in full_answer

    def test_end_tag_in_separate_chunk(self):
        """Start tag in one chunk, JSON and end tag in next."""
        src = json.dumps({"sources": [{"name": "Baz", "url": "https://baz.com"}]})
        chunks = [
            "Intro.\n[SOURCES_JSON]",
            f"{src}[/SOURCES_JSON]\nTrailing.",
        ]
        events = _collect(chunks)
        sources = _sources(events)
        assert len(sources) == 1
        assert sources[0][0]["name"] == "Baz"

    def test_json_split_across_many_chunks(self):
        """JSON content split across multiple chunks before end tag."""
        chunks = [
            "Text.\n[SOURCES_JSON]",
            '{"sources": [{"name": "X",',
            ' "url": "https://x.com"}]}',
            "[/SOURCES_JSON]\nDone.",
        ]
        events = _collect(chunks)
        sources = _sources(events)
        assert len(sources) == 1
        assert sources[0][0]["url"] == "https://x.com"
        full_answer = "".join(_answers(events))
        assert "Done." in full_answer

    def test_partial_bracket_at_end_of_chunk(self):
        """Chunk ends with '[' which is prefix of [SOURCES_JSON]."""
        src = json.dumps({"sources": [{"name": "Q", "url": "https://q.com"}]})
        chunks = [
            "Hello [",
            f"SOURCES_JSON]{src}[/SOURCES_JSON]",
        ]
        events = _collect(chunks)
        sources = _sources(events)
        assert len(sources) == 1
        assert sources[0][0]["name"] == "Q"


class TestUrlCitationDicts:
    def test_url_citation_dicts_collected_and_emitted_as_sources(self):
        chunks = [
            "Answer text.",
            {"type": "url_citation", "url": "https://cite.com", "title": "Cite Title", "content": None},
        ]
        events = _collect(chunks)
        assert _answers(events) == ["Answer text."]
        sources = _sources(events)
        assert len(sources) == 1
        assert sources[0][0]["url"] == "https://cite.com"
        assert sources[0][0]["name"] == "Cite Title"

    def test_url_citation_deduped_with_tagged_sources(self):
        """url_citation for same URL as tagged source should be deduped."""
        src = json.dumps({"sources": [{"name": "Same", "url": "https://same.com"}]})
        chunks = [
            {"type": "url_citation", "url": "https://same.com", "title": "Same", "content": None},
            f"Text.\n[SOURCES_JSON]{src}[/SOURCES_JSON]",
        ]
        events = _collect(chunks)
        sources = _sources(events)
        # Tagged source should appear, url_citation with same URL should be deduped
        all_urls = [s["url"] for grp in sources for s in grp]
        assert all_urls.count("https://same.com") == 1


class TestFilingLookupEnrichment:
    def test_filing_lookup_enriches_sources(self):
        lookup = {"SEC 10-K Filing 2024": "https://sec.gov/filing"}
        src = json.dumps({"sources": [{"name": "SEC 10-K Filing 2024"}]})
        chunks = [f"[SOURCES_JSON]{src}[/SOURCES_JSON]"]
        events = _collect(chunks, filing_lookup=lookup)
        sources = _sources(events)
        assert len(sources) == 1
        assert sources[0][0]["url"] == "https://sec.gov/filing"


class TestMultipleSourceBlocks:
    def test_two_source_blocks(self):
        src1 = json.dumps({"sources": [{"name": "A", "url": "https://a.com"}]})
        src2 = json.dumps({"sources": [{"name": "B", "url": "https://b.com"}]})
        text = f"Para 1.\n[SOURCES_JSON]{src1}[/SOURCES_JSON]\nPara 2.\n[SOURCES_JSON]{src2}[/SOURCES_JSON]"
        events = _collect([text])
        sources = _sources(events)
        assert len(sources) == 2
        assert sources[0][0]["url"] == "https://a.com"
        assert sources[1][0]["url"] == "https://b.com"


class TestEdgeCases:
    def test_malformed_json_in_tag_passes_through(self):
        """Malformed JSON inside tags should not crash."""
        chunks = ["[SOURCES_JSON]not valid json[/SOURCES_JSON]\nText after."]
        events = _collect(chunks)
        # Should not crash, text after should still appear
        full_answer = "".join(_answers(events))
        assert "Text after." in full_answer

    def test_only_url_citation_no_text(self):
        chunks = [
            {"type": "url_citation", "url": "https://only.com", "title": "Only", "content": None},
        ]
        events = _collect(chunks)
        assert _answers(events) == []
        sources = _sources(events)
        assert len(sources) == 1

    def test_buffer_flush_at_end_without_end_tag(self):
        """If stream ends mid-buffer without end tag, buffer flushed as answer."""
        chunks = ["Text.\n[SOURCES_JSON]{incomplete json"]
        events = _collect(chunks)
        # Should not crash, incomplete content flushed as answer
        full_answer = "".join(_answers(events))
        assert "Text." in full_answer


# --- Tests for _collect_paragraph_sources ---


def _collect_with_paragraphs(events_list):
    """Run _collect_paragraph_sources on a list of events."""
    return list(_collect_paragraph_sources(iter(events_list)))


def _grouped(events):
    return [e for e in events if e["type"] == "sources_grouped"]


class TestCollectParagraphSourcesBasic:
    def test_sources_grouped_emitted_at_end(self):
        events = [
            {"type": "answer", "body": "Paragraph one.\n\n"},
            {"type": "sources", "body": [{"name": "A", "url": "https://a.com"}]},
            {"type": "answer", "body": "Paragraph two.\n\n"},
            {"type": "sources", "body": [{"name": "B", "url": "https://b.com"}]},
        ]
        result = _collect_with_paragraphs(events)
        grouped = _grouped(result)
        assert len(grouped) == 1
        sources = grouped[0]["body"]["sources"]
        assert len(sources) == 2
        assert sources[0]["paragraph_indices"] == [0]
        assert sources[1]["paragraph_indices"] == [1]

    def test_inline_sources_still_pass_through(self):
        events = [
            {"type": "answer", "body": "Text.\n\n"},
            {"type": "sources", "body": [{"name": "A", "url": "https://a.com"}]},
        ]
        result = _collect_with_paragraphs(events)
        inline = [e for e in result if e["type"] == "sources"]
        assert len(inline) == 1

    def test_same_source_multiple_paragraphs_merges(self):
        events = [
            {"type": "answer", "body": "Para 1.\n\n"},
            {"type": "sources", "body": [{"name": "X", "url": "https://x.com"}]},
            {"type": "answer", "body": "Para 2.\n\n"},
            {"type": "sources", "body": [{"name": "X", "url": "https://x.com"}]},
        ]
        result = _collect_with_paragraphs(events)
        grouped = _grouped(result)
        assert grouped[0]["body"]["sources"][0]["paragraph_indices"] == [0, 1]

    def test_no_sources_no_grouped_event(self):
        events = [{"type": "answer", "body": "Just text."}]
        result = _collect_with_paragraphs(events)
        assert not any(e["type"] == "sources_grouped" for e in result)

    def test_passthrough_other_events(self):
        events = [
            {"type": "thinking_status", "body": "Analyzing..."},
            {"type": "answer", "body": "Text.\n\n"},
            {"type": "sources", "body": [{"name": "Z", "url": "https://z.com"}]},
        ]
        result = _collect_with_paragraphs(events)
        thinking = [e for e in result if e["type"] == "thinking_status"]
        assert len(thinking) == 1

    def test_source_without_url_keyed_by_name(self):
        events = [
            {"type": "answer", "body": "Text.\n\n"},
            {"type": "sources", "body": [{"name": "SEC Filing 2024"}]},
        ]
        result = _collect_with_paragraphs(events)
        grouped = _grouped(result)
        assert len(grouped) == 1
        src = grouped[0]["body"]["sources"][0]
        assert src["name"] == "SEC Filing 2024"
        assert src["url"] is None
