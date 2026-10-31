import logging
import re
from typing import Iterable, Iterator, Union

from langfuse import get_client, observe

from ai_models.model_name import ModelName
from ai_models.openrouter_client import OpenRouterClient

logger = logging.getLogger(__name__)


def _join_text_chunks(chunks: list) -> str:
    return "".join(c for c in chunks if isinstance(c, str))


def _record_usage(chunks: Iterable[Union[str, dict]]) -> Iterator[Union[str, dict]]:
    """Put the client's usage dict (served model, cost, tokens) on the current Langfuse generation; drop it."""
    for chunk in chunks:
        if isinstance(chunk, dict) and chunk.get("type") == "usage":
            usage_details = {
                key: chunk[source]
                for key, source in (
                    ("input", "prompt_tokens"),
                    ("output", "completion_tokens"),
                    ("reasoning", "reasoning_tokens"),
                )
                if isinstance(chunk.get(source), int)
            }
            cost = chunk.get("cost")
            try:
                get_client().update_current_generation(
                    model=chunk.get("model"),
                    usage_details=usage_details or None,
                    cost_details={"total": float(cost)} if isinstance(cost, (int, float)) else None,
                )
            except Exception:
                logger.warning("Failed to record LLM usage on Langfuse", exc_info=True)
            continue
        yield chunk


class MultiAgent:
    """Wrapper class for OpenRouter client to provide consistent interface across codebase"""

    def __init__(self, model_name: ModelName | None = None):
        """
        Initialize the OpenRouter client wrapper

        Args:
            model_name: The model to use (ModelName enum or string)
                       Uses generic model names - OpenRouter-specific formatting is handled internally
            api_key: OpenRouter API key (defaults to env var OPENROUTER_API_KEY)
            base_url: OpenRouter base URL (defaults to env var or https://openrouter.ai/api/v1)
        """
        self.client = OpenRouterClient(model_name=model_name)

    @property
    def model_name(self) -> str:
        """Get the current model name"""
        return self.client.model_name

    @observe(name="generate_content", as_type="generation", transform_to_string=_join_text_chunks)
    def generate_content(
        self,
        prompt: str,
        use_google_search: bool = False,
        max_tokens: int | None = None,
        reasoning: dict | None = None,
        include_usage: bool = False,
        auto_router_models: list[str] | None = None,
    ) -> Iterable[Union[str, dict]]:
        """
        Generate content using OpenRouter

        Args:
            prompt: The user prompt
            model_name: Model name to use (defaults to client's default model)
            use_google_search: If True, enables web search by appending ':online' to model name
            max_tokens: Output cap; None keeps the client default
            reasoning: OpenRouter reasoning config (e.g. {"effort": "low"}); the stream then also yields
                {"type": "reasoning", "text": ...} dicts
            include_usage: record the served model, tokens and cost on this Langfuse generation
            auto_router_models: candidate pool when the model is openrouter/auto

        Returns:
            Iterable of string chunks (str) and citation annotations (dict) when streaming
        """
        options: dict = {"max_tokens": max_tokens} if max_tokens else {}
        if reasoning is not None:
            options["reasoning"] = reasoning
        if auto_router_models:
            options["auto_router_models"] = auto_router_models
        if include_usage:
            options["include_usage"] = True
            return _record_usage(self.client.stream_chat(prompt=prompt, use_google_search=use_google_search, **options))
        return self.client.stream_chat(prompt=prompt, use_google_search=use_google_search, **options)

    def generate_text(self, prompt: str, max_tokens: int | None = None) -> str:
        """generate_content collected into one string; citation dicts are dropped.
        Blocking for the whole LLM call: async callers must offload it (asyncio.to_thread)."""
        return _join_text_chunks(list(self.generate_content(prompt=prompt, max_tokens=max_tokens)))

    @observe(name="generate_content", as_type="generation", capture_input=False, capture_output=False)
    def generate_private_content(self, prompt: str) -> Iterable[Union[str, dict]]:
        """generate_content for prompts holding a user's private data (e.g. their portfolio):
        traced for timing and model only, never the prompt or the answer."""
        return self.client.stream_chat(prompt=prompt, use_google_search=False)

    def generate_content_with_pdf_context(
        self, prompt: str, pdf_content: bytes, filename: str = "document.pdf", pdf_engine: str = "pdf-text"
    ) -> Iterable[str]:
        """
        Generate content using OpenRouter with PDF file input as context.

        Args:
            prompt: The user prompt/question about the PDF
            pdf_content: Raw bytes of the PDF file
            filename: Name of the PDF file (for context)
            pdf_engine: The PDF processing engine to use (default: "pdf-text")

        Returns:
            Iterable of string chunks when streaming
        """
        return self.client.stream_chat_with_pdf(
            prompt=prompt, pdf_content=pdf_content, filename=filename, pdf_engine=pdf_engine
        )

    def generate_content_with_pdf_url(
        self, prompt: str, pdf_url: str, filename: str = "document.pdf", pdf_engine: str = "pdf-text"
    ) -> Iterable[str]:
        """
        Generate content using OpenRouter with PDF from URL as context.

        Args:
            prompt: The user prompt/question about the PDF
            pdf_url: URL pointing to the PDF file
            filename: Name of the PDF file (for context)
            pdf_engine: The PDF processing engine to use (default: "pdf-text")

        Returns:
            Iterable of string chunks when streaming

        Example:
            >>> from ai_models.model_name import ModelName
            >>> agent = MultiAgent(model_name=ModelName.Gemini25Flash)
            >>> url = "https://bitcoin.org/bitcoin.pdf"
            >>> prompt = "What are the main points in this document?"
            >>> for chunk in agent.generate_content_with_pdf_url(prompt, url):
            ...     print(chunk, end="")
        """
        return self.client.stream_chat_with_pdf_url(
            prompt=prompt, pdf_url=pdf_url, filename=filename, pdf_engine=pdf_engine
        )

    def generate_content_by_lines(
        self,
        prompt: str,
        use_google_search: bool = False,
        max_lines: int | None = None,
        min_line_length: int = 10,
        strip_numbering: bool = True,
        strip_markdown: bool = True,
    ) -> Iterable[str]:
        """
        Generate content and yield complete lines (separated by newlines) with optional text cleanup.

        Buffers streaming chunks and yields complete lines one at a time. Useful for generating
        structured outputs like lists, questions, or multi-line responses where each line should
        be processed as a complete unit.

        Args:
            prompt: The user prompt
            use_google_search: If True, enables web search by appending ':online' to model name
            max_lines: Maximum number of lines to yield (None for unlimited)
            min_line_length: Minimum character length for a line to be yielded (filters empty/short lines)
            strip_numbering: If True, removes leading numbers like "1.", "2)", etc.
            strip_markdown: If True, removes markdown asterisks (*)

        Yields:
            Complete, cleaned lines of text one at a time

        Example:
            >>> from ai_models.model_name import ModelName
            >>> agent = MultiAgent(model_name=ModelName.Gemini25Flash)
            >>> prompt = "Generate 3 questions about Python, one per line"
            >>> for question in agent.generate_content_by_lines(prompt, max_lines=3):
            ...     print(f"Question: {question}")
        """
        buffer = ""
        lines_yielded = 0

        # Stream chunks and accumulate in buffer (skip citation dicts)
        for chunk in self.generate_content(prompt=prompt, use_google_search=use_google_search):
            if isinstance(chunk, dict):
                continue
            buffer += chunk

            # Process complete lines
            while "\n" in buffer:
                # Stop if we've reached max_lines
                if max_lines is not None and lines_yielded >= max_lines:
                    return

                line, buffer = buffer.split("\n", 1)

                # Clean the line
                clean_line = line
                if strip_numbering:
                    clean_line = re.sub(r"^\d+[\.\)\:]\s*", "", clean_line)
                if strip_markdown:
                    clean_line = clean_line.replace("*", "")
                clean_line = clean_line.strip()

                # Yield if line meets minimum length requirement
                if clean_line and len(clean_line) >= min_line_length:
                    yield clean_line
                    lines_yielded += 1

        # Process any remaining content in buffer
        if buffer.strip():
            # Stop if we've reached max_lines
            if max_lines is not None and lines_yielded >= max_lines:
                return

            clean_line = buffer
            if strip_numbering:
                clean_line = re.sub(r"^\d+[\.\)\:]\s*", "", clean_line)
            if strip_markdown:
                clean_line = clean_line.replace("*", "")
            clean_line = clean_line.strip()

            if clean_line and len(clean_line) >= min_line_length:
                yield clean_line
