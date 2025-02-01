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

    def generate_content(self, prompt, **kwargs):
        """
        Generate content using the Gemini model

        Args:
            prompt (str): The input prompt for content generation
            **kwargs: Additional arguments to pass to the generate_content method

        Raises:
            ValueError: If the prompt is empty or None
        """
        if not prompt or not isinstance(prompt, str) or prompt.strip() == "":
            raise ValueError("Prompt must be a non-empty string")
            
        return self.client.generate_content(prompt, **kwargs)
    

