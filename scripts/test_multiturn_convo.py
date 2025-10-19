import os

from dotenv import load_dotenv
from google import genai

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
chat = client.chats.create(model="gemini-2.5-flash-lite-preview-06-17")

response = chat.send_message_stream(
    "How does asset to equity ratio tell about a business? Give the answer in 10 words."
)
print("First response:")
for chunk in response:
    print(chunk.text, end="")

response = chat.send_message_stream("How is that metric calculated? Give the answer in as much details as possible.")
print("Second response:")
for chunk in response:
    print(chunk.text, end="")

# for message in chat.get_history():
#     print(f'role - {message.role}', end=": ")
#     print(message.parts[0].text)
