from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import requests

from services.analyze_retrieval.publisher import publisher_label_for
from services.analyze_retrieval.schemas import AnalyzePassage, AnalyzeSource
from services.market_recap.url_utils import canonicalize_url, source_id_for

_CHUNK_SEPARATOR = re.compile(r"\s*\[\.\.\.\]\s*")


@dataclass(frozen=True)
class UrlIngestResult:
    source: AnalyzeSource
    selected_passages: list[AnalyzePassage]
    metadata: dict[str, Any] = field(default_factory=dict)


class TavilyExtractError(Exception):
    def __init__(self, message: str, *, metadata: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.metadata = metadata or {}


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


def parse_tavily_extract_response(*, url: str, response: dict[str, Any]) -> UrlIngestResult:
    results = response.get("results") or []
    failed_results = response.get("failed_results") or []
    failed_url = None
    if isinstance(failed_results, list):
        failed_url = next(
            (item.get("url") for item in failed_results if isinstance(item, dict) and item.get("url")), None
        )

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
        passages = [
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
            for idx, chunk in enumerate(chunks, start=1)
        ]
        return UrlIngestResult(
            source=source,
            selected_passages=passages,
            metadata={
                "response_time": response.get("response_time"),
                "tavily_request_id": response.get("request_id"),
                "usage": response.get("usage"),
                "failed_url": failed_url,
            },
        )

    raise TavilyExtractError(
        "Could not read the document URL.",
        metadata={
            "response_time": response.get("response_time"),
            "tavily_request_id": response.get("request_id"),
            "usage": response.get("usage"),
            "failed_url": failed_url or url,
        },
    )


class TavilyExtractClient:
    def __init__(self, api_key: str | None = None, timeout_buffer: float = 5.0) -> None:
        self._api_key = api_key if api_key is not None else os.getenv("TAVILY_API_KEY", "")
        self._timeout_buffer = timeout_buffer

    def extract_url(
        self,
        *,
        url: str,
        query: str,
        extract_depth: str,
        chunks_per_source: int,
        format: str,
        timeout: int,
    ) -> UrlIngestResult:
        if not self._api_key:
            raise RuntimeError("TAVILY_API_KEY is not configured")

        response = requests.post(
            "https://api.tavily.com/extract",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "urls": [url],
                "query": query,
                "extract_depth": extract_depth,
                "chunks_per_source": chunks_per_source,
                "format": format,
                "timeout": timeout,
                "include_images": False,
                "include_favicon": True,
                "include_usage": True,
            },
            timeout=float(timeout) + self._timeout_buffer,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise TavilyExtractError("Could not read the document URL.")
        return parse_tavily_extract_response(url=url, response=data)
