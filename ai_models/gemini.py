import os
from typing import AsyncGenerator, Optional, Generator
from google import genai
from google.genai import types
from dotenv import load_dotenv
from ai_models.model_name import ModelName
from dataclasses import dataclass
from enum import StrEnum

class ContentType(StrEnum):
    Answer = "answer"
    Ground = "ground"
    Thought = "thought"

@dataclass(frozen=True)
class ContentGround:
    text: str
    uri: str

@dataclass(frozen=True)
class ContentPart:
    type: ContentType
    text: str
    ground: Optional[ContentGround] = None

    def __repr__(self) -> str:
        return f"Content part of type {self.type}. Text: {self.text}. Ground: {self.ground}"

load_dotenv()

class GeminiModel:
    def __init__(self, model_name: str | None=None):
        """Initialize the Gemini agent with API key configuration"""
        self.MODEL_NAME = model_name or ModelName.GeminiFlash
        self.client = genai.Client(
            api_key=os.getenv("GEMINI_API_KEY"),
        )
        self.chat = self.client.chats.create(model=self.MODEL_NAME)


    def generate_content(
        self, 
        prompt: str | list[str], 
        model_name: str | None = None,
        stream: bool = True, 
        thought: bool = False, 
        use_google_search: bool = False,
        **kwargs
    ) -> Generator[ContentPart, None, None] | types.GenerateContentResponse:
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
        
        search_tool = types.Tool(
            google_search=types.GoogleSearch()
        )
        url_context_tool = types.Tool(
            url_context=types.UrlContext()
        )

        # Extract config handling logic
        config_kwargs = {"config": kwargs["config"]} if "config" in kwargs else {}
        if use_google_search:
            if "config" not in config_kwargs:
                config_kwargs["config"] = {}
            config_kwargs["config"]["tools"] = [search_tool]

        
        if not stream:
            response = self.client.models.generate_content(
                model=model_name,
                contents=prompt,
                **config_kwargs,
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
                    tools=[search_tool] if use_google_search else []
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
                response = self.chat.send_message_stream(
                    prompt, 
                    config=base_config,
                    **{k: v for k, v in kwargs.items() if k != "config"}
                )
            else:
                response = self.chat.send_message_stream(
                    prompt,
                    **config_kwargs
                )

            # For streaming responses, yield text parts
            for chunk in response:
                for candidate in chunk.candidates:
                    if candidate.grounding_metadata != None and candidate.grounding_metadata.grounding_chunks != None:
                        for grounding_chunk in candidate.grounding_metadata.grounding_chunks:
                            yield ContentPart(
                                type=ContentType.Ground,
                                text="",
                                ground=ContentGround(
                                    text=grounding_chunk.web.title,
                                    uri=grounding_chunk.web.uri
                                )
                            )

                for part in chunk.candidates[0].content.parts:
                    if part.thought:
                        yield ContentPart(
                            type=ContentType.Thought,
                            text=part.text
                        )
                    else:
                        yield ContentPart(
                            type=ContentType.Answer,
                            text=part.text
                        )

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