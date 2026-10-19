import asyncio

import pytest

from scripts import run_recap_audio


@pytest.mark.parametrize("value", [None, "", "false", "0"])
def test_disabled_by_default_exits_zero_without_touching_connectors(monkeypatch, value):
    def boom():
        raise AssertionError("connectors must not be built when disabled")

    monkeypatch.setattr(run_recap_audio, "MarketRecapConnector", boom)
    monkeypatch.setattr(run_recap_audio, "TickerRecapConnector", boom)
    env = {} if value is None else {"RECAP_AUDIO_ENABLED": value}

    code = asyncio.run(run_recap_audio._run(cadence="daily", kind="all", limit=1, since_days=3, dry_run=True, env=env))

    assert code == 0


@pytest.mark.parametrize("value", ["true", "TRUE", "1", "yes"])
def test_enabled_values(value):
    assert run_recap_audio._audio_enabled({"RECAP_AUDIO_ENABLED": value})
