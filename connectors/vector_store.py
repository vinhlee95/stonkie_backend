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