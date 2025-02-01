import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

class GeminiModel:
    def __init__(self, temperature=0.3, max_output_tokens=150, system_instruction=None):
        """Initialize the Gemini agent with API key configuration"""
        
        self.api_key = os.getenv("GEMINI_API_KEY")

        if not self.api_key:
            raise ValueError("GEMINI_API_KEY environment variable not found")
        
        genai.configure(api_key=self.api_key)

        if not system_instruction:
            self.client = genai.GenerativeModel(
                model_name="gemini-1.5-flash",
                generation_config=genai.GenerationConfig(
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                )
            )
        else:
            self.client = genai.GenerativeModel(
                model_name="gemini-1.5-flash",
                generation_config=genai.GenerationConfig(
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                ),
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
            
        return self.client.generate_content_async(prompt, stream=stream, **kwargs)
    

