import os
from pinecone import Pinecone

pc = Pinecone(api_key=os.getenv('PINECONE_API_KEY'))

def search_similar_content(query_embeddings: list[float], index_name: str, filter: dict, top_k: int = 20):
  index = pc.Index(index_name)
  results = index.query(
      vector=query_embeddings,
      top_k=top_k,
      include_metadata=True,
      filter=filter
  )
  
  return results

# TODO: support page number in the matches
def search_similar_content_and_format_to_texts(query_embeddings: list[float], index_name: str, filter: dict, top_k: int = 20):
  results = search_similar_content(query_embeddings, index_name, filter, top_k)
  
  context_from_official_document = ""
  if results and results['matches']:
    for match in results['matches']:
      text = match['metadata']['text'].strip()
      context_from_official_document += f"{text}\n\n"

  return context_from_official_document