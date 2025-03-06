from openai import OpenAI

# Only support generate embedding for now
class OpenAIModel:
  def __init__(self):
    self.client = OpenAI()

  def generate_embedding(self, input: str, model: str = "text-embedding-3-small"):
    response = self.client.embeddings.create(
        model=model,
        input=input,
        encoding_format="float"
    )
    
    return response.data[0].embedding

  def generate_content(self):
    raise NotImplemented()
