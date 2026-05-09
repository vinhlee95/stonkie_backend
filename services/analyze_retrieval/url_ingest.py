import json
import logging
from typing import Any, Protocol

from connectors.tavily_extract_client import TavilyExtractClient, TavilyExtractError, UrlIngestResult

logger = logging.getLogger(__name__)
_EXCERPT_LOG_PREVIEW_CHARS = 500


class UrlIngestError(Exception):
    pass


class TavilyExtractClientProtocol(Protocol):
    def extract_url(
        self,
        *,
        url: str,
        query: str,
        extract_depth: str,
        chunks_per_source: int,
        format: str,
        timeout: int,
    ) -> UrlIngestResult: ...


def _extract_settings(source_kind: str) -> tuple[str, int]:
    if source_kind == "filing":
        return "advanced", 30
    return "basic", 10


def _log_ingest(
    *,
    request_id: str,
    query: str,
    requested_url: str,
    metadata: dict[str, Any] | None,
    successful_url: str | None,
    failed_url: str | None,
    chunk_count: int,
    result: UrlIngestResult | None,
) -> None:
    metadata = metadata or {}
    payload = {
        "event": "analyze_v2_url_ingest",
        "provider": "tavily",
        "request_id": request_id,
        "query": query,
        "requested_url": requested_url,
        "successful_url": successful_url,
        "failed_url": failed_url,
        "chunk_count": chunk_count,
        "source_id": result.source.id if result else None,
        "source": (
            {
                "source_id": result.source.id,
                "url": result.source.url,
                "title": result.source.title,
                "publisher": result.source.publisher,
            }
            if result
            else None
        ),
        "excerpt_previews": (
            [
                {
                    "excerpt_index": passage.passage_index,
                    "char_count": len(passage.content),
                    "preview": passage.content[:_EXCERPT_LOG_PREVIEW_CHARS],
                }
                for passage in result.selected_passages
            ]
            if result
            else []
        ),
        "response_time": metadata.get("response_time"),
        "tavily_request_id": metadata.get("tavily_request_id"),
        "usage": metadata.get("usage"),
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
    try:
        result = client.extract_url(
            url=url,
            query=question,
            extract_depth=extract_depth,
            chunks_per_source=5,
            format="markdown",
            timeout=timeout,
        )
    except TavilyExtractError as exc:
        metadata = exc.metadata
        _log_ingest(
            request_id=request_id,
            query=question,
            requested_url=url,
            metadata=metadata,
            successful_url=None,
            failed_url=metadata.get("failed_url") or url,
            chunk_count=0,
            result=None,
        )
        raise UrlIngestError("Could not read the document URL.") from exc
    except Exception as exc:
        _log_ingest(
            request_id=request_id,
            query=question,
            requested_url=url,
            metadata=None,
            successful_url=None,
            failed_url=url,
            chunk_count=0,
            result=None,
        )
        raise UrlIngestError("Could not read the document URL.") from exc

    _log_ingest(
        request_id=request_id,
        query=question,
        requested_url=url,
        metadata=result.metadata,
        successful_url=result.source.url,
        failed_url=result.metadata.get("failed_url"),
        chunk_count=len(result.selected_passages),
        result=result,
    )

    if not result.selected_passages:
        raise UrlIngestError("Could not read the document URL.")

    return result
