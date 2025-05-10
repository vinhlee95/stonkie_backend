import os
from typing import AsyncGenerator
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

class GeminiModel:
    def __init__(self, system_instruction=None):
        """Initialize the Gemini agent with API key configuration"""
        
        self.api_key = os.getenv("GEMINI_API_KEY")
        # MODEL_NAME = "gemini-2.5-flash-preview-04-17"
        MODEL_NAME = "gemini-2.0-flash"

        if not self.api_key:
            raise ValueError("GEMINI_API_KEY environment variable not found")
        
        genai.configure(api_key=self.api_key)

        if not system_instruction:
            self.client = genai.GenerativeModel(
                model_name=MODEL_NAME,
            )
        else:
            self.client = genai.GenerativeModel(
                model_name=MODEL_NAME,
                system_instruction=system_instruction
            )


    def generate_content(self, prompt: str | list[str], stream: bool = True, **kwargs):
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
            return self.client.generate_content(prompt, **kwargs)
            
        return self.client.generate_content_async(prompt, stream=stream, **kwargs)
    
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
        response = await self.generate_content(prompt, **kwargs)
        buffer = ""
        
        async for chunk in response:
            if chunk.text:
                buffer += chunk.text
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