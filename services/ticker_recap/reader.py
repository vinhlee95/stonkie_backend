from connectors.ticker_recap import TickerRecapConnector, TickerRecapDto


def get_latest_recaps(
    ticker: str,
    cadence: str,
    *,
    limit: int = 1,
    connector: TickerRecapConnector | None = None,
) -> list[TickerRecapDto]:
    """Return the latest precomputed recaps for a ticker+cadence.

    Service layer over TickerRecapConnector so the presentation/router layer never
    touches the connector directly (3-layer: presentation -> service -> connector).
    The connector is injectable so tests pass a fake."""
    connector = connector or TickerRecapConnector()
    return connector.get_latest(ticker, cadence, limit=limit)
