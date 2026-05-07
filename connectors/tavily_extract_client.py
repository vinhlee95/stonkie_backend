from __future__ import annotations

import os
from typing import Any

import requests


class TavilyExtractClient:
    def __init__(self, api_key: str | None = None, timeout: float = 35.0) -> None:
        self._api_key = api_key if api_key is not None else os.getenv("TAVILY_API_KEY", "")
        self._timeout = timeout

    def extract(
        self,
        *,
        urls: list[str],
        query: str,
        extract_depth: str,
        chunks_per_source: int,
        format: str,
        timeout: int,
    ) -> dict[str, Any]:
        if not self._api_key:
            raise RuntimeError("TAVILY_API_KEY is not configured")

        response = requests.post(
            "https://api.tavily.com/extract",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "urls": urls,
                "query": query,
                "extract_depth": extract_depth,
                "chunks_per_source": chunks_per_source,
                "format": format,
                "timeout": timeout,
                "include_images": False,
                "include_favicon": True,
                "include_usage": True,
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}
