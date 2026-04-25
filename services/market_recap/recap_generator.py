import json
from dataclasses import dataclass
from datetime import date

from agent.multi_agent import MultiAgent
from services.market_recap.schemas import Citation, RecapPayload, RetrievalResult, Source


@dataclass(frozen=True)
class GeneratorResult:
    payload: RecapPayload
    model: str
    raw_model_output: str


class GeneratorError(Exception):
    pass


def _market_label(market: str) -> str:
    market_key = market.upper()
    if market_key == "VN":
        return "Vietnam"
    if market_key == "US":
        return "US"
    return market_key


def _build_prompt(retrieval: RetrievalResult, market: str, period_start: date, period_end: date) -> str:
    market_label = _market_label(market)
    market_key = market.upper()
    lines = [
        f"Generate a weekly {market_label} market recap JSON.",
        f"Period start: {period_start.isoformat()}",
        f"Period end: {period_end.isoformat()}",
        "Use ONLY this schema:",
        '{"summary":"string","bullets":[{"text":"string","source_indices":[0]}]}',
        "Rules:",
        "- summary must be non-empty plain text.",
        "- bullets must contain 3-6 items.",
        "- each bullet must include at least one integer index in source_indices.",
        "- source_indices must reference only provided Source [i] blocks.",
        "- do not emit keys outside summary/bullets/text/source_indices.",
        "- avoid generic global-market-only narrative; ground claims in provided market-specific sources.",
        "Return JSON wrapped in [RECAP_JSON]...[/RECAP_JSON].",
    ]
    if market_key == "VN":
        lines.extend(
            [
                "Phân tích thị trường chứng khoán Việt Nam trong tuần vừa qua.",
                "Tập trung vào: dòng tiền và thanh khoản thị trường, nhóm ngành dẫn dắt/tụt hậu, và bối cảnh vĩ mô.",
                "For Vietnam market recaps, coverage MUST explicitly include:",
                "- VN-Index trend for the week (direction and key driver).",
                "- macroeconomic context (inflation, FX, rates, policy, or growth data if available).",
                "- market money flow / liquidity behavior (foreign net buy-sell, sector rotation, or turnover).",
                "If you cannot satisfy all required VN sections from provided sources, output MUST fail safe.",
                'MUST fail safe format: {"summary":"","bullets":[]}',
                "Do not fabricate VN-specific metrics or entities not grounded in provided sources.",
                "If you cannot satisfy all required VN sections, do not output partial recap.",
            ]
        )
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
    start_tag = "[RECAP_JSON]"
    end_tag = "[/RECAP_JSON]"
    start = raw_text.find(start_tag)
    end = raw_text.find(end_tag)
    if start < 0 or end < 0 or end <= start:
        raise GeneratorError("missing recap json markers")
    return raw_text[start + len(start_tag) : end]


def generate_recap(
    retrieval: RetrievalResult,
    *,
    market: str = "US",
    period_start: date,
    period_end: date,
    agent: MultiAgent | None = None,
) -> GeneratorResult:
    llm = agent or MultiAgent()
    prompt = _build_prompt(retrieval, market, period_start, period_end)
    chunks = llm.generate_content(prompt=prompt, use_google_search=False)
    raw_text = "".join(chunk for chunk in chunks if isinstance(chunk, str))
    payload_json = json.loads(_extract_json_block(raw_text))

    bullets = payload_json.get("bullets", [])
    summary = payload_json.get("summary", "")
    citations = []
    for bullet in bullets:
        for source_index in bullet.get("source_indices", []):
            if source_index < 0 or source_index >= len(retrieval.candidates):
                raise GeneratorError("source index out of range")
            citations.append(source_index)

    sources_by_id: dict[str, Source] = {}
    payload_bullets = []
    for bullet in bullets:
        bullet_citations = []
        for source_index in bullet.get("source_indices", []):
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

    payload = RecapPayload(
        period_start=period_start,
        period_end=period_end,
        summary=summary,
        bullets=payload_bullets,
        sources=list(sources_by_id.values()),
    )
    return GeneratorResult(payload=payload, model=getattr(llm, "model_name", ""), raw_model_output=raw_text)
