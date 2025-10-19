import logging
import os
import time

from pinecone import Pinecone

logger = logging.getLogger(__name__)

pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))


def search_similar_content(query_embeddings: list[float], index_name: str, filter: dict, top_k: int = 10):
    index = pc.Index(index_name)
    results = index.query(vector=query_embeddings, top_k=top_k, include_metadata=True, filter=filter)

    return results


# TODO: support page number in the matches
def search_similar_content_and_format_to_texts(
    query_embeddings: list[float], index_name: str, filter: dict, top_k: int = 20
):
    results = search_similar_content(query_embeddings, index_name, filter, top_k)

    context_from_official_document = ""
    if results and results["matches"]:
        for match in results["matches"]:
            if match.get("score") >= 0.5:
                text = match["metadata"]["text"].strip()
                context_from_official_document += f"{text}\n\n"

    return context_from_official_document


def init_vector_record(id: str, embeddings: list[float], metadata: dict):
    return {"id": id, "values": embeddings, "metadata": metadata}


def add_vector_record_by_batch(index_name: str, records: list[dict], batch_size: int = 100) -> None:
    index = pc.Index(index_name)

    pinecone_start = time.time()

    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]
        index.upsert(vectors=batch)

    pinecone_end = time.time()
    logger.info(
        f"Uploading to Pinecone index {index_name} took {pinecone_end - pinecone_start:.2f} seconds for {len(records)} records"
    )
