import os
from typing import List

from dotenv import load_dotenv
from openai import OpenAI
from pinecone import Pinecone


def get_embeddings(text: str) -> List[float]:
    """Generate embeddings for text using OpenAI's text-embedding-3-small model."""
    client = OpenAI()
    response = client.embeddings.create(model="text-embedding-3-small", input=text, encoding_format="float")
    return response.data[0].embedding


def init_pinecone():
    """Initialize Pinecone client."""
    pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
    return pc.Index("company10k")


def search_similar_content(query: str, top_k: int = 5):
    """Search for similar content in Pinecone database."""
    # Generate embedding for the query
    query_embedding = get_embeddings(query)

    # Initialize Pinecone and search
    index = init_pinecone()
    results = index.query(vector=query_embedding, top_k=top_k, include_metadata=True)

    return results


def main():
    # Load environment variables
    load_dotenv()

    while True:
        # Get user input
        print("\nEnter your question (or 'quit' to exit):")
        query = input("> ").strip()

        if query.lower() == "quit":
            break

        if not query:
            continue

        try:
            # Search for similar content
            results = search_similar_content(query)

            # Display results
            print("\nTop similar passages:")
            print("-" * 80)

            for i, match in enumerate(results["matches"], 1):
                score = match["score"]
                text = match["metadata"]["text"]
                ticker = match["metadata"]["ticker"]

                print(f"\n{i}. Score: {score:.4f}")
                print(f"Company: {ticker}")
                print(f"Content: {text[:300]}...")  # Show first 300 chars
                print("-" * 80)

        except Exception as e:
            print(f"Error: {str(e)}")


if __name__ == "__main__":
    main()
