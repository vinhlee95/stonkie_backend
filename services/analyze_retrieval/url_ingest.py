import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from connectors.tavily_extract_client import TavilyExtractClient
from services.analyze_retrieval.publisher import publisher_label_for
from services.analyze_retrieval.schemas import AnalyzePassage, AnalyzeSource
from services.market_recap.url_utils import canonicalize_url, source_id_for

logger = logging.getLogger(__name__)

_CHUNK_SEPARATOR = re.compile(r"\s*\[\.\.\.\]\s*")


class UrlIngestError(Exception):
    pass


class TavilyExtractClientProtocol(Protocol):
    def extract(
        self,
        *,
        urls: list[str],
        query: str,
        extract_depth: str,
        chunks_per_source: int,
        format: str,
        timeout: int,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class UrlIngestResult:
    sources: list[AnalyzeSource]
    selected_passages: list[AnalyzePassage]


def _extract_settings(source_kind: str) -> tuple[str, int]:
    if source_kind == "filing":
        return "advanced", 30
    return "basic", 10


def _split_chunks(raw_content: str) -> list[str]:
    chunks: list[str] = []
    seen: set[str] = set()
    for part in _CHUNK_SEPARATOR.split(raw_content or ""):
        chunk = " ".join(part.split())
        if not chunk or chunk in seen:
            continue
        seen.add(chunk)
        chunks.append(chunk)
    return chunks


def _log_ingest(
    *,
    request_id: str,
    query: str,
    requested_urls: list[str],
    response: dict[str, Any] | None,
    successful_urls: list[str],
    failed_urls: list[str],
    chunk_count: int,
    sources: list[AnalyzeSource],
) -> None:
    response = response or {}
    payload = {
        "event": "analyze_v2_url_ingest",
        "provider": "tavily",
        "request_id": request_id,
        "query": query,
        "requested_urls": requested_urls,
        "successful_urls": successful_urls,
        "failed_urls": failed_urls,
        "chunk_count": chunk_count,
        "selected_source_ids": [source.id for source in sources],
        "sources": [
            {
                "source_id": source.id,
                "url": source.url,
                "title": source.title,
                "publisher": source.publisher,
            }
            for source in sources
        ],
        "response_time": response.get("response_time"),
        "tavily_request_id": response.get("request_id"),
        "usage": response.get("usage"),
    }
    logger.info(json.dumps(payload, sort_keys=True), extra=payload)


def ingest_url(
    *,
    url: str,
    question: str,
    request_id: str,
    source_kind: str,
    client: TavilyExtractClientProtocol | None = None,
) -> UrlIngestResult:
    client = client or TavilyExtractClient()
    extract_depth, timeout = _extract_settings(source_kind)
    requested_urls = [url]
    response: dict[str, Any] | None = None
    try:
        response = client.extract(
            urls=requested_urls,
            query=question,
            extract_depth=extract_depth,
            chunks_per_source=5,
            format="markdown",
            timeout=timeout,
        )
    except Exception as exc:
        _log_ingest(
            request_id=request_id,
            query=question,
            requested_urls=requested_urls,
            response=response,
            successful_urls=[],
            failed_urls=requested_urls,
            chunk_count=0,
            sources=[],
        )
        raise UrlIngestError("Could not read the document URL.") from exc

    results = response.get("results") or []
    failed_results = response.get("failed_results") or []
    sources: list[AnalyzeSource] = []
    passages: list[AnalyzePassage] = []
    successful_urls: list[str] = []
    failed_urls: list[str] = []

    if isinstance(failed_results, list):
        failed_urls = [item.get("url", "") for item in failed_results if isinstance(item, dict) and item.get("url")]

    for item in results:
        if not isinstance(item, dict):
            continue
        result_url = item.get("url") or url
        chunks = _split_chunks(str(item.get("raw_content") or ""))
        if not chunks:
            continue

        canonical_url = canonicalize_url(result_url)
        source_id = source_id_for(canonical_url)
        title = str(item.get("title") or "Extracted document")
        source = AnalyzeSource(
            id=source_id,
            url=canonical_url,
            title=title,
            publisher=publisher_label_for(canonical_url),
            published_at=datetime.now(UTC),
            is_trusted=True,
            raw_content="\n\n".join(chunks),
        )
        sources.append(source)
        successful_urls.append(canonical_url)
        for idx, chunk in enumerate(chunks, start=1):
            passages.append(
                AnalyzePassage(
                    source_id=source_id,
                    url=canonical_url,
                    title=title,
                    publisher=source.publisher,
                    published_at=source.published_at,
                    is_trusted=source.is_trusted,
                    passage_index=idx,
                    content=chunk,
                )
            )

    _log_ingest(
        request_id=request_id,
        query=question,
        requested_urls=requested_urls,
        response=response,
        successful_urls=successful_urls,
        failed_urls=failed_urls,
        chunk_count=len(passages),
        sources=sources,
    )

    if not sources or not passages:
        raise UrlIngestError("Could not read the document URL.")

    return UrlIngestResult(sources=sources, selected_passages=passages)
