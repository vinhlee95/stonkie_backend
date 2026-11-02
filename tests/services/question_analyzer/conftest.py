"""Search always runs in the v2 stock handlers, so no handler test may reach Brave: retrieval fails offline by
default. Tests that need sources patch `_retrieve_routed` / `retrieve_for_analyze` themselves (their patch wins)."""

import pytest

from services.analyze_retrieval.schemas import BraveRetrievalError


def _offline_retrieval(**_kwargs):
    raise BraveRetrievalError("no network in tests")


@pytest.fixture(autouse=True)
def _no_brave_in_tests(monkeypatch):
    monkeypatch.setattr("services.question_analyzer.handlers_v2.retrieve_for_analyze", _offline_retrieval)
    monkeypatch.setattr("services.question_analyzer.comparison_handler_v2.retrieve_for_analyze", _offline_retrieval)
