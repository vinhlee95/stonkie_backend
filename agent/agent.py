from typing import Any, Iterable, Literal, Union

from ai_models.gemini import ContentPart, GeminiModel
from ai_models.openai import OpenAIModel

SupportedModel = Literal["gemini", "openai"]


class Agent:
    """Wrapper class to abstract different AI model implementations"""

    def __init__(self, model_type: SupportedModel = "gemini", model_name: str | None = None):
        """
        Initialize the AI model wrapper

        Args:
            model_type (str): Type of AI model to use ("gemini", "openai", etc.)
        """
        self.model_type = model_type
        if model_type == "gemini":
            self.model = GeminiModel(model_name=model_name)
        elif model_type == "openai":
            self.model = OpenAIModel(model_name=model_name)
        else:
            raise ValueError(f"Unsupported model type: {model_type}")

    # TODO: have common typing for return type of this for both openai & Gemini models
    def generate_content(
        self,
        prompt: str | list[str],
        model_name: str | None = None,
        stream=True,
        thought: bool = False,
        use_google_search: bool = False,
        use_url_context: bool = False,
        **kwargs,
    ) -> Union[Iterable[ContentPart], Any]:
        """
        Generate content using the configured AI model

        Returns:
            Generated content from the AI model - when streaming, returns iterable of ContentPart objects
        """
        return self.model.generate_content(
            prompt,
            model_name=model_name,
            stream=stream,
            thought=thought,
            use_google_search=use_google_search,
            use_url_context=use_url_context,
            **kwargs,
        )

    def generate_embedding(self, input: str, model: str = "text-embedding-3-small"):
        return self.model.generate_embedding(input=input, model=model)
