from google import genai
from google.genai import types
import os

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
prompt = "What is the sum of the first 50 prime numbers?"

thoughts = ""
answer = ""

for chunk in client.models.generate_content_stream(
    model="gemini-2.5-flash-preview-05-20",
    contents=prompt,
    config=types.GenerateContentConfig(
      thinking_config=types.ThinkingConfig(
        include_thoughts=True
      )
    )
):
  for part in chunk.candidates[0].content.parts:
    if not part.text:
      continue
    elif part.thought:
      if not thoughts:
        print("Thoughts summary:")
      print(part.text)
      thoughts += part.text
    else:
      if not answer:
        print("Thoughts summary:")
      print(part.text)
      answer += part.text
      