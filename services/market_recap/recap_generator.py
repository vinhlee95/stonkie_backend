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


def _build_prompt(retrieval: RetrievalResult, period_start: date, period_end: date) -> str:
    lines = [
        "Generate a weekly US market recap JSON.",
        f"Period start: {period_start.isoformat()}",
        f"Period end: {period_end.isoformat()}",
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
    period_start: date,
    period_end: date,
    agent: MultiAgent | None = None,
) -> GeneratorResult:
    llm = agent or MultiAgent()
    prompt = _build_prompt(retrieval, period_start, period_end)
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
