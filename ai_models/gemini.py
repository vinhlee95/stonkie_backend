import os
from typing import AsyncGenerator
from google import genai
from google.genai import types
from dotenv import load_dotenv
from ai_models.model_name import ModelName

load_dotenv()

class GeminiModel:
    def __init__(self, model_name: str | None=None):
        """Initialize the Gemini agent with API key configuration"""
        self.MODEL_NAME = model_name or ModelName.GeminiFlash
        self.client = genai.Client(
            api_key=os.getenv("GEMINI_API_KEY"),
        )


    def generate_content(
        self, 
        prompt: str | list[str], 
        model_name: str | None = None,
        stream: bool = True, 
        thought: bool = False, 
        **kwargs
    ):
        """
        Generate content using the Gemini model

        Args:
            prompt (str | list[str]): The input prompt for content generation. Can be either a single string
                                     or a list of strings.
            model_name (str | None): The model to use for generation. If None, uses default model.
            stream (bool): Whether to stream the response. If False, returns a synchronous response.
            thought (bool): Whether to include thinking process in the response.
            **kwargs: Additional arguments to pass to the generate_content method

        Returns:
            If stream=False: Returns the raw response object or parsed JSON if response_mime_type is "application/json"
            If stream=True: Returns a generator that yields text parts

        Raises:
            ValueError: If the prompt is empty, None, or contains empty strings
        """
        if isinstance(prompt, str):
            if not prompt or prompt.strip() == "":
                raise ValueError("Prompt must be a non-empty string")
        elif isinstance(prompt, list):
            if not prompt or any(not isinstance(p, str) or not p.strip() for p in prompt):
                raise ValueError("Prompt list must contain non-empty strings")
        else:
            raise ValueError("Prompt must be either a string or a list of strings")

        model_name = model_name or self.MODEL_NAME
        
        # Extract config handling logic
        config_kwargs = {"config": kwargs["config"]} if "config" in kwargs else {}
        
        if not stream:
            response = self.client.models.generate_content(
                model=model_name,
                contents=prompt,
                **config_kwargs
            )

            if config_kwargs.get("config", {}).get("response_mime_type") == "application/json":
                return response.parsed

            return response

        def stream_generator():
            if thought:
                base_config = types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(
                        include_thoughts=True,
                        thinking_budget=1024,
                    ),
                )
                # Merge with config from kwargs if it exists
                if "config" in kwargs:
                    base_config = types.GenerateContentConfig(
                        thinking_config=types.ThinkingConfig(
                            include_thoughts=True,
                            thinking_budget=1024,
                        ),
                        **kwargs["config"]
                    )
                response = self.client.models.generate_content_stream(
                    model=model_name,
                    contents=prompt,
                    config=base_config,
                    **{k: v for k, v in kwargs.items() if k != "config"}
                )
            else:
                response = self.client.models.generate_content_stream(
                    model=model_name,
                    contents=prompt,
                    **config_kwargs
                )

            # For streaming responses, yield text parts
            for chunk in response:
                for part in chunk.candidates[0].content.parts:
                    yield part

        return stream_generator()
    
    async def generate_content_and_normalize_results(self, prompt, model_name: str | None = None, **kwargs) -> AsyncGenerator[str, None]:
        """
        Generate content and normalize the streaming results by processing complete lines
        and cleaning up the output format.
        
        Args:
            prompt: The input prompt
            **kwargs: Additional parameters for content generation
            
        Yields:
            str: Cleaned and normalized text chunks
        """
        buffer = ""
        for part in self.generate_content(prompt, model_name, **kwargs):
            buffer += part.text
            # Process complete lines if we have a newline
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                clean_line = line.replace("*", "").strip()
                if clean_line:
                    yield clean_line
        
        # Don't forget to process any remaining text in the buffer
        if buffer:
            clean_line = buffer.replace("*", "").strip()
            if clean_line:
                yield clean_line      