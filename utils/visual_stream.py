"""Utilities for splitting streamed answer text into typed text/visual events."""

from dataclasses import dataclass
from typing import Dict, Generator, Optional


@dataclass
class _VisualState:
    block_id: str
    lang: str
    close_token: str
    include_close_token_in_content: bool
    content: str = ""
    full_content: str = ""


class VisualAnswerStreamSplitter:
    """Stateful splitter for ```html / ```svg fenced blocks in streamed answer text.

    Limitations:
    - Nested backtick fences inside a visual block are not supported. A ``` sequence
      inside HTML/SVG content (e.g. an embedded code example) will prematurely close
      the block. Fenced openers use case-sensitive matching; <html> uses case-insensitive.
    """

    OPENERS = ("```html\n", "```svg\n")
    CLOSER = "```"
    HTML_TAG_OPENER = "<html"
    HTML_TAG_CLOSER = "</html>"

    def __init__(self) -> None:
        self._text_buffer = ""
        self._visual: Optional[_VisualState] = None
        self._counter = 0

    def process_text(self, text: str) -> Generator[Dict, None, None]:
        """Consume a text chunk and emit answer/answer_visual_* events."""
        if not text:
            return

        if self._visual is None:
            self._text_buffer += text
        else:
            self._visual.content += text

        while True:
            if self._visual is None:
                match = self._find_opener(self._text_buffer)
                if match is None:
                    flush_upto = len(self._text_buffer) - self._tail_prefix_len(self._text_buffer)
                    if flush_upto > 0:
                        plain = self._text_buffer[:flush_upto]
                        self._text_buffer = self._text_buffer[flush_upto:]
                        if plain:
                            yield {"type": "answer", "body": plain}
                    break

                idx, opener_kind, opener = match
                if idx > 0:
                    plain = self._text_buffer[:idx]
                    if plain:
                        yield {"type": "answer", "body": plain}

                self._counter += 1
                block_id = f"vis_{self._counter}"
                if opener_kind == "html_tag":
                    lang = "html"
                    self._visual = _VisualState(
                        block_id=block_id,
                        lang=lang,
                        close_token=self.HTML_TAG_CLOSER,
                        include_close_token_in_content=True,
                        content="",
                    )
                else:
                    lang = "html" if "html" in opener else "svg"
                    self._visual = _VisualState(
                        block_id=block_id,
                        lang=lang,
                        close_token=self.CLOSER,
                        include_close_token_in_content=False,
                        content="",
                    )
                yield {"type": "answer_visual_start", "body": {"block_id": block_id, "lang": lang}}
                self._text_buffer = self._text_buffer[idx + len(opener) :]
                if opener_kind == "html_tag":
                    # Keep <html...> in visual content for iframe rendering.
                    self._visual.content += opener + self._text_buffer
                else:
                    self._visual.content += self._text_buffer
                self._text_buffer = ""

            if self._visual is not None:
                close_idx = self._visual.content.lower().find(self._visual.close_token.lower())
                if close_idx == -1:
                    # Hold back len(close_token)-1 bytes so a partial close token
                    # split across process_text calls is not flushed prematurely.
                    hold = len(self._visual.close_token) - 1
                    flush_upto = len(self._visual.content) - hold
                    if flush_upto > 0:
                        delta = self._visual.content[:flush_upto]
                        self._visual.full_content += delta
                        self._visual.content = self._visual.content[flush_upto:]
                        yield {
                            "type": "answer_visual_delta",
                            "body": {"block_id": self._visual.block_id, "delta": delta},
                        }
                    break

                close_end = close_idx + len(self._visual.close_token)
                content_end = close_end if self._visual.include_close_token_in_content else close_idx
                if content_end > 0:
                    delta = self._visual.content[:content_end]
                    self._visual.full_content += delta
                    yield {
                        "type": "answer_visual_delta",
                        "body": {"block_id": self._visual.block_id, "delta": delta},
                    }

                full_content = self._visual.full_content
                remainder = self._visual.content[close_end:]
                done = {
                    "type": "answer_visual_done",
                    "body": {
                        "block_id": self._visual.block_id,
                        "lang": self._visual.lang,
                        "content": full_content,
                    },
                }
                self._visual = None
                yield done

                if remainder:
                    self._text_buffer += remainder
                continue

            break

    def finalize(self) -> Generator[Dict, None, None]:
        """Flush any buffered data at stream end."""
        if self._visual is not None:
            message = f"Incomplete visual block for {self._visual.lang}; falling back to plain text."
            yield {
                "type": "answer_visual_error",
                "body": {
                    "block_id": self._visual.block_id,
                    "lang": self._visual.lang,
                    "message": message,
                },
            }
            if self._visual.close_token == self.HTML_TAG_CLOSER:
                fallback = self._visual.full_content + self._visual.content
            else:
                fallback = f"```{self._visual.lang}\n{self._visual.full_content}{self._visual.content}"
            if fallback:
                yield {"type": "answer", "body": fallback}
            self._visual = None

        if self._text_buffer:
            yield {"type": "answer", "body": self._text_buffer}
            self._text_buffer = ""

    @classmethod
    def _find_opener(cls, text: str):
        candidates = []
        lower_text = text.lower()
        for opener in cls.OPENERS:
            idx = text.find(opener)
            if idx != -1:
                candidates.append((idx, "fenced", opener))

        html_idx = lower_text.find(cls.HTML_TAG_OPENER)
        if html_idx != -1:
            # Preserve the original case/attributes at the opener boundary.
            html_open_segment = text[html_idx : html_idx + len(cls.HTML_TAG_OPENER)]
            candidates.append((html_idx, "html_tag", html_open_segment))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0])
        return candidates[0]

    @classmethod
    def _tail_prefix_len(cls, text: str) -> int:
        max_len = 0
        for opener in cls.OPENERS:
            max_check = min(len(opener) - 1, len(text))
            for i in range(1, max_check + 1):
                if opener.startswith(text[-i:]):
                    max_len = max(max_len, i)

        html_opener = cls.HTML_TAG_OPENER
        lower_text = text.lower()
        max_check = min(len(html_opener) - 1, len(text))
        for i in range(1, max_check + 1):
            if html_opener.startswith(lower_text[-i:]):
                max_len = max(max_len, i)
        return max_len
