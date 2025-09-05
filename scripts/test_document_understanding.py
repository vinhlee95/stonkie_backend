from google import genai
from google.genai import types
import httpx
import os

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

doc_url = "https://d18rn0p25nwr6d.cloudfront.net/CIK-0000320193/a411a029-368f-4479-b416-25c404acca3d.pdf"

# Retrieve and encode the PDF byte
# doc_data = httpx.get(doc_url).content

# prompt = "Summarize this document"
# response = client.models.generate_content(
#   model="gemini-2.5-flash",
#   contents=[
#       types.Part.from_bytes(
#         data=doc_data,
#         mime_type='application/pdf',
#       ),
#       prompt])

# print(response.text)

from google import genai
from google.genai.types import Tool, GenerateContentConfig

tools = [
  {"url_context": {}},
]

apple_filing_url = 'https://investor.apple.com/sec-filings/default.aspx'
response = client.models.generate_content(
    model='gemini-2.5-flash',
    contents=f"Summarise this PDF document: {doc_url}",
    config=GenerateContentConfig(
        tools=tools,
    )
)

for each in response.candidates[0].content.parts:
    print(each.text)
