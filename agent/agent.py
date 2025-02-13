from typing import AsyncGenerator
from ai_models.gemini import GeminiModel


class Agent:
    """Wrapper class to abstract different AI model implementations"""
    
    def __init__(self, model_type="gemini"):
        """
        Initialize the AI model wrapper
        
        Args:
            model_type (str): Type of AI model to use ("gemini", "openai", etc.)
        """
        self.model_type = model_type
        if model_type == "gemini":
            self.model = GeminiModel()
        else:
            raise ValueError(f"Unsupported model type: {model_type}")
    
    # TODO: deprecate this and use generate_content_and_normalize_results instead
    def generate_content(self, prompt, system_instruction=None, stream=True, **kwargs):
        """
        Generate content using the configured AI model
        
        Args:
            prompt (str | list[str]): Input prompt for content generation
            system_instruction (str, optional): System instruction for model behavior
            **kwargs: Additional parameters for the model
            
        Returns:
            Generated content from the AI model
        """
        if system_instruction:
            return self.model.generate_content(
                system_instruction=system_instruction,
                prompt=prompt,
                stream=stream,
                **kwargs
            )
        
        return self.model.generate_content(prompt, stream=stream, **kwargs)
    
    async def generate_content_and_normalize_results(self, prompt, **kwargs) -> AsyncGenerator[str, None]:
        """
        Generate content using the configured AI model and normalize the results
        """
        content_generator = self.model.generate_content_and_normalize_results(prompt, **kwargs)
        async for content in content_generator:
            yield content
