import json
from dataclasses import dataclass
from datetime import date

from agent.multi_agent import MultiAgent
from services.market_recap.schemas import Citation, RetrievalResult, Source
from services.ticker_recap.schemas import TickerRecapPayload


@dataclass(frozen=True)
class GeneratorResult:
    payload: TickerRecapPayload
    model: str
    raw_model_output: str


class GeneratorError(Exception):
    pass


def _build_prompt(
    retrieval: RetrievalResult,
    *,
    ticker: str,
    company_name: str,
    price_change: dict | None,
    period_start: date,
) -> str:
    move_line = "Price move: unavailable."
    if price_change is not None and price_change.get("change_percent") is not None:
        move_line = (
            f"Price move on {price_change.get('trading_date')}: close {price_change.get('close')} "
            f"vs prev {price_change.get('prev_close')} ({price_change['change_percent']:+.2f}%)."
        )
    lines = [
        f"Generate a daily news recap JSON for the single stock {company_name} ({ticker}) "
        f"for {period_start.isoformat()}.",
        move_line,
        "Use ONLY this schema:",
        '{"summary":"string","bullets":[{"text":"string","source_indices":[0]}]}',
        "Rules:",
        "- summary must be non-empty plain text about THIS stock and its move today.",
        "- bullets must contain 3-6 items, each explaining a driver of the move or key news.",
        "- each bullet must include at least one integer index in source_indices.",
        "- source_indices must reference only provided Source [i] blocks.",
        f"- ground every claim in the sources; do not invent numbers or events for {ticker}.",
        "- focus on THIS ticker; ignore broad-market filler unless it directly explains the move.",
        "Return JSON wrapped in [RECAP_JSON]...[/RECAP_JSON].",
    ]
    for idx, candidate in enumerate(retrieval.candidates):
        lines.extend(
            [
                f"Source [{idx}]",
                f"Title: {candidate.title}",
                f"URL: {candidate.url}",
                f"Published: {candidate.published_date.isoformat() if candidate.published_date else 'null'}",
                f"Content: {candidate.raw_content}",
            ]
        )
    return "\n".join(lines)


def _extract_json_block(raw_text: str) -> str:
    start_tag, end_tag = "[RECAP_JSON]", "[/RECAP_JSON]"
    start, end = raw_text.find(start_tag), raw_text.find(end_tag)
    if start < 0 or end < 0 or end <= start:
        raise GeneratorError("missing recap json markers")
    return raw_text[start + len(start_tag) : end]


def generate_recap(
    retrieval: RetrievalResult,
    *,
    ticker: str,
    company_name: str,
    price_change: dict | None,
    period_start: date,
    period_end: date,
    cadence: str = "daily",
    agent: MultiAgent | None = None,
) -> GeneratorResult:
    llm = agent or MultiAgent()
    prompt = _build_prompt(
        retrieval,
        ticker=ticker,
        company_name=company_name,
        price_change=price_change,
        period_start=period_start,
    )
    chunks = llm.generate_content(prompt=prompt, use_google_search=False)
    raw_text = "".join(chunk for chunk in chunks if isinstance(chunk, str))
    payload_json = json.loads(_extract_json_block(raw_text))

    summary = payload_json.get("summary", "")
    bullets = payload_json.get("bullets", [])

    sources_by_id: dict[str, Source] = {}
    payload_bullets = []
    for bullet in bullets:
        bullet_citations = []
        for source_index in bullet.get("source_indices", []):
            if source_index < 0 or source_index >= len(retrieval.candidates):
                raise GeneratorError(f"source index out of range: {source_index}")
            candidate = retrieval.candidates[source_index]
            source = Source(
                id=candidate.source_id,
                url=candidate.canonical_url,
                title=candidate.title,
                publisher=candidate.canonical_url.split("/")[2] if "://" in candidate.canonical_url else "",
                published_at=candidate.published_date,
                fetched_at=candidate.published_date,
            )
            sources_by_id[source.id] = source
            bullet_citations.append(Citation(source_id=source.id))
        payload_bullets.append({"text": bullet.get("text", ""), "citations": bullet_citations})

    payload = TickerRecapPayload(
        ticker=ticker,
        cadence=cadence,
        period_start=period_start,
        period_end=period_end,
        summary=summary,
        bullets=payload_bullets,
        sources=list(sources_by_id.values()),
    )
    return GeneratorResult(payload=payload, model=getattr(llm, "model_name", ""), raw_model_output=raw_text)
