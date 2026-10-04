import pytest

from utils.answer_sanitizer import MAX_HOLD, AnswerSanitizer, sanitize


def stream(chunks: list[str]) -> str:
    s = AnswerSanitizer()
    return "".join(s.feed(c) for c in chunks) + s.flush()


def test_sanitize_strips_images_links_tags_and_urls():
    text = "Up ![x](https://evil.test/?d=63313) see [Reuters](https://r.com) <img src=x> or https://evil.test/a and www.x.io ok"
    assert sanitize(text) == "Up  see Reuters  or  and  ok"


@pytest.mark.parametrize("size", [1, 2, 3, 7])
def test_split_across_chunks_still_stripped(size):
    text = "TSLA fell 7%. ![x](https://evil.test/?d=63313) [src](https://r.com) <b>bold</b> https://evil.test/p done.\n"
    chunks = [text[i : i + size] for i in range(0, len(text), size)]

    assert stream(chunks) == sanitize(text)
    assert "evil" not in stream(chunks)


def test_plain_text_streams_through_with_last_word_held():
    s = AnswerSanitizer()
    assert s.feed("Apple rose 2% ") == "Apple rose 2% "
    assert s.feed("toda") == ""
    assert s.feed("y.\n") == "today.\n"


def test_stray_bracket_does_not_hold_everything():
    s = AnswerSanitizer()
    out = s.feed("[" + "a " * MAX_HOLD)
    assert len(out) > 0
    assert len(out) + len(s.flush()) == 1 + 2 * MAX_HOLD
