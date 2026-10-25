"""ETFDataOptimizer: requirement levels and a non-blocking DB read (fake connector, no DB)."""

from __future__ import annotations

import threading

import pytest

from services.etf_question_analyzer.data_optimizer import ETFDataOptimizer
from services.etf_question_analyzer.types import ETFDataRequirement
from tests.services.etf_question_analyzer.etf_fakes import make_etf


class _Connector:
    def __init__(self, etf=None):
        self.etf = etf
        self.threads: list[int] = []

    def get_by_ticker(self, ticker: str):
        self.threads.append(threading.get_ident())
        return self.etf


@pytest.mark.asyncio
@pytest.mark.parametrize("requirement", [ETFDataRequirement.BASIC, ETFDataRequirement.DETAILED])
async def test_fetch_reads_the_row_in_a_worker_thread(requirement):
    connector = _Connector(make_etf())
    result = await ETFDataOptimizer(connector=connector).fetch_optimized_data("SXR8", requirement)
    assert result == make_etf()
    assert connector.threads and connector.threads[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_none_requirement_and_invalid_ticker_skip_the_read():
    connector = _Connector(make_etf())
    optimizer = ETFDataOptimizer(connector=connector)
    assert await optimizer.fetch_optimized_data("SXR8", ETFDataRequirement.NONE) is None
    assert await optimizer.fetch_optimized_data("undefined", ETFDataRequirement.BASIC) is None
    assert connector.threads == []


@pytest.mark.asyncio
async def test_missing_row_returns_none():
    assert (
        await ETFDataOptimizer(connector=_Connector(None)).fetch_optimized_data("NOPE", ETFDataRequirement.BASIC)
        is None
    )
