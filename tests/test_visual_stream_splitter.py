from utils.visual_stream import VisualAnswerStreamSplitter


def collect(splitter: VisualAnswerStreamSplitter, chunks: list[str]):
    events = []
    for chunk in chunks:
        events.extend(list(splitter.process_text(chunk)))
    events.extend(list(splitter.finalize()))
    return events


def test_splits_text_and_visual_events():
    splitter = VisualAnswerStreamSplitter()
    events = collect(
        splitter,
        [
            "Revenue grew strongly.\n\n```html\n<div>chart part 1",
            " and part 2</div>\n```\n\nMargins expanded.",
        ],
    )

    assert events[0] == {"type": "answer", "body": "Revenue grew strongly.\n\n"}
    assert events[1]["type"] == "answer_visual_start"
    assert events[1]["body"]["lang"] == "html"
    assert events[2]["type"] == "answer_visual_delta"
    assert events[3]["type"] == "answer_visual_delta"
    assert events[4]["type"] == "answer_visual_done"
    assert events[4]["body"]["content"] == "<div>chart part 1 and part 2</div>\n"
    assert events[5] == {"type": "answer", "body": "\n\nMargins expanded."}


def test_handles_split_fence_openers():
    splitter = VisualAnswerStreamSplitter()
    events = collect(
        splitter,
        [
            "Intro ```ht",
            "ml\n<svg-like html></svg-like html>\n``` Outro",
        ],
    )

    assert events[0] == {"type": "answer", "body": "Intro "}
    assert events[1]["type"] == "answer_visual_start"
    assert events[2]["type"] == "answer_visual_delta"
    assert events[3]["type"] == "answer_visual_done"
    assert events[4] == {"type": "answer", "body": " Outro"}


def test_unclosed_visual_falls_back_with_error():
    splitter = VisualAnswerStreamSplitter()
    events = collect(splitter, ["Before\n```svg\n<svg><rect></svg>"])

    assert events[0] == {"type": "answer", "body": "Before\n"}
    assert events[1]["type"] == "answer_visual_start"
    assert events[2]["type"] == "answer_visual_delta"
    assert events[3]["type"] == "answer_visual_error"
    assert events[4]["type"] == "answer"
    assert events[4]["body"].startswith("```svg\n")


def test_detects_raw_html_visual_block():
    splitter = VisualAnswerStreamSplitter()
    events = collect(
        splitter,
        [
            "Analysis before chart.\n\n<html><head><script>1</script></head><body><div>Chart</div></body></html>\n\n",
            "After chart text.",
        ],
    )

    assert events[0] == {"type": "answer", "body": "Analysis before chart.\n\n"}
    assert events[1]["type"] == "answer_visual_start"
    assert events[1]["body"]["lang"] == "html"
    assert events[2]["type"] == "answer_visual_delta"
    assert events[2]["body"]["delta"].startswith("<html>")
    assert events[3]["type"] == "answer_visual_done"
    assert events[3]["body"]["content"].endswith("</html>")
    trailing_text = "".join(e["body"] for e in events[4:] if e["type"] == "answer")
    assert trailing_text == "\n\nAfter chart text."


def test_detects_html_opener_split_across_chunks():
    splitter = VisualAnswerStreamSplitter()
    events = collect(
        splitter,
        [
            "Before chart\n<ht",
            "ml><body>x</body></html> end",
        ],
    )

    assert events[0] == {"type": "answer", "body": "Before chart\n"}
    assert events[1]["type"] == "answer_visual_start"
    assert events[2]["type"] == "answer_visual_delta"
    assert events[3]["type"] == "answer_visual_done"
    assert events[4] == {"type": "answer", "body": " end"}


def test_visual_done_contains_full_accumulated_content():
    splitter = VisualAnswerStreamSplitter()
    events = collect(
        splitter,
        [
            "<html><body><div>part-1",
            " part-2",
            " part-3</div></body></html>",
        ],
    )

    done = [e for e in events if e["type"] == "answer_visual_done"][0]
    assert done["body"]["content"] == "<html><body><div>part-1 part-2 part-3</div></body></html>"


def test_fenced_visual_done_does_not_include_closing_fence():
    splitter = VisualAnswerStreamSplitter()
    events = collect(splitter, ["```html\n<div>chart</div>\n```"])

    done = [e for e in events if e["type"] == "answer_visual_done"][0]
    assert done["body"]["content"] == "<div>chart</div>\n"
    assert "```" not in done["body"]["content"]
