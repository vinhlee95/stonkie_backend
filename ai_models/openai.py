from openai import OpenAI
import json
from typing import Optional, Any
from pydantic import BaseModel
from enum import Enum

class ContentType(str, Enum):
    TEXT = "text"
    CHART = "chart"

class ReportModel(BaseModel):
  type: ContentType
  content: str
  data: Optional[dict[str, Any]] = None

ReportOutput = list[ReportModel]

# Only support generate embedding for now
class OpenAIModel:
  # Use o3 mini model for output generation
  def __init__(self, model: str = "gpt-4o-mini"):
    self.client = OpenAI()
    self.model = model

  def generate_embedding(self, input: str, model: str = "text-embedding-3-small"):
    response = self.client.embeddings.create(
        model=model,
        input=input,
        encoding_format="float"
    )
    
    return response.data[0].embedding

  def generate_content(self, user_input: str, stream: bool = False):
    with self.client.responses.stream(
        model=self.model,
        input=[
            {
                "role": "user",
                "content": user_input,
            },
        ],
        # text_format=ReportOutput,
    ) as stream:
        for event in stream:
            if event.type == "response.refusal.delta":
                print(event.delta, end="")
            elif event.type == "response.output_text.delta":
                print(event.delta, end="")
            elif event.type == "response.error":
                print(event.error, end="")
            elif event.type == "response.completed":
                print("Completed")
                # print(event.response.output)

        final_response = stream.get_final_response()
        
        try:
            if final_response.output_text:
                # Clean the string by removing markdown code block markers
                cleaned_text = final_response.output_text.strip()
                if cleaned_text.startswith('```json'):
                    cleaned_text = cleaned_text[7:]  # Remove '```json'
                if cleaned_text.endswith('```'):
                    cleaned_text = cleaned_text[:-3]  # Remove '```'
                cleaned_text = cleaned_text.strip()
                return json.loads(cleaned_text)
            return None
        except json.JSONDecodeError as e:
            print(f"Error parsing JSON: {e}")
            return None

        