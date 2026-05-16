from __future__ import annotations

import asyncio
import uuid
from dataclasses import asdict
from typing import TYPE_CHECKING

from services.analyze_retrieval.retrieval import retrieve_for_analyze
from services.analyze_retrieval.url_ingest import ingest_url

if TYPE_CHECKING:
    from connectors.brave_client import BraveClient
    from connectors.company import CompanyConnector
    from connectors.company_financial import CompanyFinancialConnector


async def brave_search(
    *,
    query: str,
    brave_client: BraveClient,
    ticker: str = "UNKNOWN",
    company_name: str | None = None,
    market: str = "GLOBAL",
) -> dict:
    request_id = str(uuid.uuid4())
    result = await asyncio.to_thread(
        retrieve_for_analyze,
        question=query,
        market=market,
        request_id=request_id,
        brave_client=brave_client,
        ticker=ticker,
        company_name=company_name,
    )
    sources_dicts = [
        {
            "url": s.url,
            "title": s.title,
            "publisher": s.publisher,
            "published_at": s.published_at.isoformat() if s.published_at else None,
            "is_trusted": s.is_trusted,
        }
        for s in result.sources
    ]
    passages_dicts = [
        {
            "source_id": p.source_id,
            "url": p.url,
            "title": p.title,
            "content": p.content,
        }
        for p in result.selected_passages
    ]
    return {
        "sources": sources_dicts,
        "passages": passages_dicts,
        "analyze_sources": result.sources,
    }


async def get_financial_data(
    *,
    ticker: str,
    connector: CompanyFinancialConnector,
    statement_type: str = "all",
    period_type: str = "annual",
    num_periods: int = 3,
) -> list[dict]:
    if period_type == "quarterly":
        statements = await asyncio.to_thread(
            connector.get_company_quarterly_financial_statements_recent, ticker, num_periods
        )
    else:
        statements = await asyncio.to_thread(connector.get_company_financial_statements_recent, ticker, num_periods)

    results = []
    for stmt in statements:
        stmt_dict = connector.to_dict(stmt)
        if statement_type != "all":
            stmt_dict = connector.get_company_statement_by_type(stmt_dict, statement_type)
        results.append(stmt_dict)
    return results


async def get_company_profile(
    *,
    ticker: str,
    connector: CompanyConnector,
) -> dict | None:
    dto = await asyncio.to_thread(connector.get_fundamental_data, ticker)
    if dto is None:
        return None
    return asdict(dto)


async def read_url(
    *,
    url: str,
    question: str,
    source_kind: str = "article",
) -> dict:
    request_id = str(uuid.uuid4())
    result = await asyncio.to_thread(
        ingest_url,
        url=url,
        question=question,
        request_id=request_id,
        source_kind=source_kind,
    )
    content = "\n\n".join(p.content for p in result.selected_passages)
    return {
        "content": content,
        "source": {
            "url": result.source.url,
            "title": result.source.title,
            "publisher": result.source.publisher,
        },
        "analyze_source": result.source,
    }
