import json

from openai import OpenAI

from ai_models.model_name import ModelName


class OpenAIModel:
    def __init__(self, model_name: str | None = None):
        # https://platform.openai.com/docs/models
        self.client = OpenAI()
        self.model = model_name or ModelName.Gpt4Mini

    def generate_embedding(self, input: str, model: str = ModelName.TextEmbeddingSmall):
        response = self.client.embeddings.create(model=model, input=input, encoding_format="float")

        return response.data[0].embedding

    def _generate_content_sync(self, user_input: str, model_name: str | None):
        with self.client.responses.stream(
            model=model_name or self.model,
            input=[
                {
                    "role": "user",
                    "content": user_input,
                },
            ],
        ) as stream:
            final_response = stream.get_final_response()

            try:
                if final_response.output_text:
                    # Clean the string by removing markdown code block markers
                    cleaned_text = final_response.output_text.strip()
                    if cleaned_text.startswith("```json"):
                        cleaned_text = cleaned_text[7:]  # Remove '```json'
                    if cleaned_text.endswith("```"):
                        cleaned_text = cleaned_text[:-3]  # Remove '```'
                    cleaned_text = cleaned_text.strip()
                    return json.loads(cleaned_text)
                return None
            except json.JSONDecodeError as e:
                print(f"Error parsing JSON: {e}")
                return None

    async def _generate_content_async(self, user_input: str, model_name: str | None):
        with self.client.responses.stream(
            model=model_name or self.model,
            input=[
                {
                    "role": "user",
                    "content": user_input,
                },
            ],
        ) as stream:
            for event in stream:
                if event.type == "response.refusal.delta":
                    print(event.delta, end="")
                elif event.type == "response.output_text.delta":
                    cleaned_text = event.delta
                    if cleaned_text.startswith("```json"):
                        cleaned_text = cleaned_text[7:]  # Remove '```json'
                    if cleaned_text.endswith("```"):
                        cleaned_text = cleaned_text[:-3]  # Remove '```'

                    yield cleaned_text
                elif event.type == "response.error":
                    print(event.error, end="")
                elif event.type == "response.completed":
                    print("Completed")

    def generate_content(self, user_input: str, model_name: str | None = None, stream: bool = True, **kwargs):
        if stream:
            return self._generate_content_async(user_input, model_name)
        else:
            return self._generate_content_sync(user_input, model_name)
