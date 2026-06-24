"""Throwaway demo: exercise services.ticker_recap.query_generator against the REAL LLM.

Run: source venv/bin/activate && PYTHONPATH=. python scripts/try_ticker_query.py
NOT a test (hits OpenRouter). Just prints the move-routing + generated query per example.
"""

from dotenv import load_dotenv

from agent.multi_agent import MultiAgent
from services.ticker_recap.query_generator import (
    BIG_MOVE_THRESHOLD_PCT,
    QueryGenerationError,
    generate_query,
)


def _pc(change_percent: float | None, close: float, prev_close: float) -> dict | None:
    if change_percent is None:
        return None
    return {
        "trading_date": "2026-06-18",
        "close": close,
        "prev_close": prev_close,
        "change": round(close - prev_close, 2),
        "change_percent": change_percent,
        "currency": "USD",
    }


EXAMPLES = [
    ("NVDA", "NVIDIA Corporation", _pc(6.2, 142.5, 134.2)),
    ("TSLA", "Tesla, Inc.", _pc(-8.1, 210.0, 228.5)),
    ("AAPL", "Apple Inc.", _pc(0.3, 200.6, 200.0)),
    ("GOOG", "Alphabet Inc.", _pc(-1.4, 175.0, 177.5)),
    ("GOOG", "Alphabet Inc.", None),
]


def main() -> int:
    load_dotenv()
    agent = MultiAgent()
    for ticker, name, pc in EXAMPLES:
        pct = pc.get("change_percent") if pc else None
        if pct is None:
            routing = "neutral (no price)"
        elif abs(pct) >= BIG_MOVE_THRESHOLD_PCT:
            routing = f"BIG ({pct:+.2f}%, |pct| >= {BIG_MOVE_THRESHOLD_PCT})"
        else:
            routing = f"flat ({pct:+.2f}%, |pct| < {BIG_MOVE_THRESHOLD_PCT})"
        try:
            query = generate_query(ticker, name, pc, agent=agent)
        except QueryGenerationError as exc:
            query = f"<QueryGenerationError: {exc}>"
        print(f"{ticker:5s} | {routing:34s} | {query}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
