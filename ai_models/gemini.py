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
        stream: bool = True, 
        thought: bool = False, 
        **kwargs
    ):
        """
        Generate content using the Gemini model

        Args:
            prompt (str | list[str]): The input prompt for content generation. Can be either a single string
                                     or a list of strings.
            **kwargs: Additional arguments to pass to the generate_content method

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
        
        if stream == False:
            return self.client.models.generate_content(
                model=self.MODEL_NAME,
                contents=prompt,
            )

        if thought:
            return self.client.models.generate_content_stream(
                model=self.MODEL_NAME,
                contents=prompt,
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(
                        include_thoughts=True
                    )
                ),
                **kwargs
            )

        return self.client.models.generate_content_stream(
                model=self.MODEL_NAME,
                contents=prompt,
                **kwargs
            )
    
    async def generate_content_and_normalize_results(self, prompt, **kwargs) -> AsyncGenerator[str, None]:
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
        
        for chunk in self.generate_content(prompt, **kwargs):
            for part in chunk.candidates[0].content.parts:
                if part.text:
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