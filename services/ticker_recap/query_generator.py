from agent.multi_agent import MultiAgent

BIG_MOVE_THRESHOLD_PCT = 3.0


class QueryGenerationError(Exception):
    """Raised when the LLM produces no usable search query."""


def generate_query(
    ticker: str,
    company_name: str,
    price_change: dict | None,
    *,
    agent: MultiAgent | None = None,
) -> str:
    agent = agent or MultiAgent()
    prompt = _build_prompt(ticker, company_name, price_change)
    chunks = agent.generate_content(prompt=prompt, use_google_search=False)
    raw = "".join(chunk for chunk in chunks if isinstance(chunk, str)).strip()
    query = raw.strip('"').strip()
    if not query:
        raise QueryGenerationError("LLM returned an empty query")
    return query.splitlines()[0].strip()


def _build_prompt(ticker: str, company_name: str, price_change: dict | None) -> str:
    lines = [
        f"You craft ONE web search query to find news explaining today's stock move " f"for {company_name} ({ticker}).",
        "Return ONLY the raw search query text. No quotes, no preamble, no explanation.",
    ]
    pct = price_change.get("change_percent") if price_change else None
    if pct is None:
        lines.append(f"No price data available; produce a neutral query for the latest {ticker} news.")
        return "\n".join(lines)

    lines.append(
        f"Trading date: {price_change.get('trading_date')}. "
        f"Close {price_change.get('close')} vs prev {price_change.get('prev_close')} ({pct:+.2f}%)."
    )
    if abs(pct) >= BIG_MOVE_THRESHOLD_PCT:
        direction = "jumped" if pct > 0 else "fell"
        lines.append(
            f"This is a BIG move. Frame the query causally, e.g. why {ticker} {direction} "
            f"{abs(pct):.1f}% today / what drove the move."
        )
    else:
        lines.append(f"This is a small/flat move. A neutral 'latest {ticker} stock news today' framing is fine.")
    return "\n".join(lines)
