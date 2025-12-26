import re
from typing import Iterable

from ai_models.model_name import ModelName
from ai_models.openrouter_client import OpenRouterClient


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

    def generate_content(
        self,
        prompt: str,
        use_google_search: bool = False,
    ) -> Iterable[str]:
        """
        Generate content using OpenRouter

        Args:
            prompt: The user prompt
            model_name: Model name to use (defaults to client's default model)
            use_google_search: If True, enables web search by appending ':online' to model name

        Returns:
            Iterable of string chunks when streaming
        """
        return self.client.stream_chat(prompt=prompt, use_google_search=use_google_search)

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
            >>> agent = MultiAgent(model_name=ModelName.Gemini25FlashLite)
            >>> prompt = "Generate 3 questions about Python, one per line"
            >>> for question in agent.generate_content_by_lines(prompt, max_lines=3):
            ...     print(f"Question: {question}")
        """
        buffer = ""
        lines_yielded = 0

        # Stream chunks and accumulate in buffer
        for chunk in self.generate_content(prompt=prompt, use_google_search=use_google_search):
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
