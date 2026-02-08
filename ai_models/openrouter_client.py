import base64
import logging
import os
from typing import Dict, Iterable, Union

from openai import OpenAI

from ai_models.model_name import ModelName

logger = logging.getLogger(__name__)

# OpenRouter-specific model name mappings
# OpenRouter requires 'google/' prefix for Gemini models
OPENROUTER_MODEL_MAP: Dict[ModelName, str] = {
    # Map generic Gemini names to OpenRouter format
    ModelName.Gemini25FlashLite: "google/gemini-2.5-flash-lite",
    ModelName.Gemini25Flash: "google/gemini-2.5-flash",
    ModelName.Gemini30Flash: "google/gemini-3-flash-preview",
    # OpenRouter Auto Router for automatic model selection
    ModelName.Auto: "openrouter/auto",
    # Fastest model with :nitro variant for high-speed inference
    # Uses Gemini 2.5 Flash Lite (fastest model) with nitro variant
    # See: https://openrouter.ai/docs/guides/routing/model-variants/nitro
    ModelName.Fastest: "google/gemini-2.5-flash-lite:nitro",
}


def get_openrouter_model_name(model_name: ModelName) -> str:
    """Convert generic model name to OpenRouter-specific format.

    Args:
        model_name: Generic model name (e.g., 'gemini-2.5-flash-lite')

    Returns:
        OpenRouter-specific model name (e.g., 'google/gemini-2.5-flash-lite')
        Falls back to original name if no mapping exists.
    """
    return OPENROUTER_MODEL_MAP.get(model_name, model_name)


class OpenRouterClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, model_name: ModelName | None = None):
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY is required to use OpenRouterClient")

        self.base_url = base_url or os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        # Convert generic model name to OpenRouter-specific format
        DEFAULT_MODEL_NAME = ModelName.Gemini30Flash
        generic_name = model_name or DEFAULT_MODEL_NAME
        self.model_name = get_openrouter_model_name(generic_name)
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def stream_chat(self, prompt: str, use_google_search: bool = False) -> Iterable[Union[str, dict]]:
        """
        Stream chat completions as plain text chunks and citation dicts.

        Args:
            prompt: The user prompt
            model: Model name (defaults to self.default_model)
            use_google_search: If True, appends ':online' to model name to enable web search

        Yields:
            str for text chunks, dict for url_citation annotations
        """
        # model_name is already in OpenRouter format from __init__
        chosen_model = self.model_name

        # OpenRouter enables web search by appending ':online' to the model name
        # Strip existing variants (e.g. :nitro) first — can't chain variants
        if use_google_search and not chosen_model.endswith(":online"):
            base_model = chosen_model.split(":")[0] if ":" in chosen_model else chosen_model
            chosen_model = f"{base_model}:online"

        extra_body = {}
        if use_google_search:
            extra_body["plugins"] = [{"id": "web", "max_results": 3}]

        logger.info(f"OpenRouter stream_chat: model={chosen_model}, google_search={use_google_search}")

        try:
            response = self.client.chat.completions.create(
                model=chosen_model,
                messages=[{"role": "user", "content": prompt}],
                stream=True,
                **({"extra_body": extra_body} if extra_body else {}),
            )
        except Exception as e:
            logger.error(f"OpenRouter API error (model={chosen_model}): {e}")
            raise

        for event in response:
            delta = event.choices[0].delta
            if delta.content:
                yield delta.content
            # OpenRouter extension: url_citation annotations in model_extra
            annotations = (delta.model_extra or {}).get("annotations", [])
            for ann in annotations:
                if ann.get("type") == "url_citation":
                    citation = ann.get("url_citation", {})
                    yield {
                        "type": "url_citation",
                        "url": citation.get("url", ""),
                        "title": citation.get("title"),
                        "content": citation.get("content"),
                    }

    # Add this new method to the OpenRouterClient class
    def stream_chat_with_pdf(
        self, prompt: str, pdf_content: bytes, filename: str = "document.pdf", pdf_engine: str = "pdf-text"
    ) -> Iterable[str]:
        """
        Stream chat completions with PDF file input as plain text chunks.

        Args:
            prompt: The user prompt/question about the PDF
            pdf_content: Raw bytes of the PDF file
            filename: Name of the PDF file (for context)
            pdf_engine: PDF parsing engine - "pdf-text" (default, faster) or "mistral-ocr" (slower, more accurate)

        Yields:
            String chunks from the streaming response

        Example:
            >>> client = OpenRouterClient()
            >>> with open("report.pdf", "rb") as f:
            >>>     pdf_bytes = f.read()
            >>> for chunk in client.stream_chat_with_pdf("Summarize this report", pdf_bytes):
            >>>     print(chunk, end="")
        """
        # Encode PDF to base64
        base64_pdf = base64.b64encode(pdf_content).decode("utf-8")
        data_url = f"data:application/pdf;base64,{base64_pdf}"

        # Prepare model name with optional online search
        chosen_model = self.model_name

        # Structure messages with both text and file
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "file", "file": {"filename": filename, "file_data": data_url}},
                ],
            }
        ]

        # Configure PDF processing engine
        plugins = [
            {
                "id": "file-parser",
                "pdf": {
                    "engine": pdf_engine  # "pdf-text" or "mistral-ocr"
                },
            }
        ]

        # Stream the response
        response = self.client.chat.completions.create(
            model=chosen_model,
            messages=messages,  # type: ignore
            extra_body={"plugins": plugins},  # OpenAI client passes extra params via extra_body
            stream=True,
        )

        for event in response:
            delta = event.choices[0].delta.content
            if delta:
                yield delta

    def stream_chat_with_pdf_url(
        self, prompt: str, pdf_url: str, filename: str = "document.pdf", pdf_engine: str = "pdf-text"
    ) -> Iterable[str]:
        """
        Stream chat completions with PDF from URL as plain text chunks.

        Args:
            prompt: The user prompt/question about the PDF
            pdf_url: URL pointing to the PDF file
            filename: Name of the PDF file (for context)
            pdf_engine: PDF parsing engine - "pdf-text" (default, faster) or "mistral-ocr" (slower, more accurate)

        Yields:
            String chunks from the streaming response

        Example:
            >>> client = OpenRouterClient()
            >>> url = "https://example.com/report.pdf"
            >>> for chunk in client.stream_chat_with_pdf_url("Summarize this report", url):
            >>>     print(chunk, end="")
        """
        # Prepare model name
        chosen_model = self.model_name

        # Structure messages with text and file URL
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "file", "file": {"filename": filename, "file_data": pdf_url}},
                ],
            }
        ]

        # Configure PDF processing engine
        plugins = [
            {
                "id": "file-parser",
                "pdf": {
                    "engine": pdf_engine  # "pdf-text" or "mistral-ocr"
                },
            }
        ]

        # Stream the response
        response = self.client.chat.completions.create(
            model=chosen_model,
            messages=messages,  # type: ignore
            extra_body={"plugins": plugins},  # OpenAI client passes extra params via extra_body
            stream=True,
        )

        for event in response:
            delta = event.choices[0].delta.content
            if delta:
                yield delta
