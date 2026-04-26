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
    if market_key == "FI":
        return "Finland"
    if market_key == "US":
        return "US"
    return market_key


def _build_prompt(
    retrieval: RetrievalResult,
    market: str,
    period_start: date,
    period_end: date,
    cadence: str = "weekly",
) -> str:
    market_label = _market_label(market)
    market_key = market.upper()
    is_daily = cadence.lower() == "daily"
    if market_key == "VN":
        if is_daily:
            lines = [
                "Tạo bản tóm tắt phiên giao dịch thị trường chứng khoán Việt Nam (daily) dưới dạng JSON.",
                f"Ngày phiên: {period_start.isoformat()}",
                "Chỉ được dùng đúng schema sau:",
                '{"summary":"string","bullets":[{"text":"string","source_indices":[0]}]}',
                "Quy tắc:",
                "- summary phải là văn bản thuần không rỗng.",
                "- bullets phải có từ 3 đến 6 ý.",
                "- mỗi bullet phải có ít nhất một chỉ số nguyên trong source_indices.",
                "- source_indices chỉ được tham chiếu các khối Source [i] đã cung cấp.",
                "- không xuất thêm key ngoài summary/bullets/text/source_indices.",
                "- tránh mô tả chung chung thị trường toàn cầu; mọi nhận định phải bám theo nguồn đã cung cấp cho thị trường Việt Nam.",
                "- chỉ tập trung vào diễn biến trong phiên giao dịch đó (mở cửa, trong phiên, đóng cửa).",
                "Yêu cầu nội dung bắt buộc cho phiên giao dịch:",
                "- diễn biến VN-Index trong phiên (chiều hướng và động lực chính).",
                "- bối cảnh vĩ mô liên quan trong ngày (nếu có trong nguồn).",
                "- hành vi dòng tiền/thanh khoản trong phiên (khối ngoại mua-bán ròng, luân chuyển nhóm ngành, giá trị giao dịch).",
                "Nếu không thể đáp ứng đầy đủ các mục bắt buộc từ nguồn đã cho, phải fail-safe.",
                'Định dạng fail-safe bắt buộc: {"summary":"","bullets":[]}',
                "Không được bịa số liệu hoặc thực thể cụ thể cho thị trường Việt Nam nếu không có trong nguồn.",
                "Trả về JSON được bọc trong [RECAP_JSON]...[/RECAP_JSON].",
            ]
        else:
            lines = [
                "Tạo bản tóm tắt thị trường chứng khoán Việt Nam theo tuần dưới dạng JSON.",
                f"Ngày bắt đầu kỳ: {period_start.isoformat()}",
                f"Ngày kết thúc kỳ: {period_end.isoformat()}",
                "Chỉ được dùng đúng schema sau:",
                '{"summary":"string","bullets":[{"text":"string","source_indices":[0]}]}',
                "Quy tắc:",
                "- summary phải là văn bản thuần không rỗng.",
                "- bullets phải có từ 3 đến 6 ý.",
                "- mỗi bullet phải có ít nhất một chỉ số nguyên trong source_indices.",
                "- source_indices chỉ được tham chiếu các khối Source [i] đã cung cấp.",
                "- không xuất thêm key ngoài summary/bullets/text/source_indices.",
                "- tránh mô tả chung chung thị trường toàn cầu; mọi nhận định phải bám theo nguồn đã cung cấp cho thị trường Việt Nam.",
                "- phải tổng hợp được diễn biến xuyên suốt cả tuần (đầu tuần, giữa tuần, cuối tuần nếu có dữ liệu), không chỉ tập trung vào một ngày đơn lẻ.",
                "Yêu cầu nội dung bắt buộc cho thị trường Việt Nam:",
                "- xu hướng VN-Index trong tuần (chiều hướng và động lực chính).",
                "- bối cảnh vĩ mô (lạm phát, tỷ giá, lãi suất, chính sách hoặc dữ liệu tăng trưởng nếu có trong nguồn).",
                "- hành vi dòng tiền/thanh khoản (khối ngoại mua-bán ròng, luân chuyển nhóm ngành, hoặc giá trị giao dịch).",
                "Nếu không thể đáp ứng đầy đủ các mục bắt buộc từ nguồn đã cho, phải fail-safe.",
                'Định dạng fail-safe bắt buộc: {"summary":"","bullets":[]}',
                "Không được bịa số liệu hoặc thực thể cụ thể cho thị trường Việt Nam nếu không có trong nguồn.",
                "Trả về JSON được bọc trong [RECAP_JSON]...[/RECAP_JSON].",
            ]
    elif market_key == "FI":
        if is_daily:
            lines = [
                "Generate a daily Finland market recap JSON for a single trading session.",
                f"Session date: {period_start.isoformat()}",
                "Use ONLY this schema:",
                '{"summary":"string","bullets":[{"text":"string","source_indices":[0]}]}',
                "Rules:",
                "- summary must be non-empty plain text.",
                "- bullets must contain 3-6 items.",
                "- each bullet must include at least one integer index in source_indices.",
                "- source_indices must reference only provided Source [i] blocks.",
                "- do not emit keys outside summary/bullets/text/source_indices.",
                "- focus strictly on the single trading session (open, intra-day moves, close), not multi-day arcs.",
                "- Prioritize OMX Helsinki and Finnish listed equities.",
                "- Include Finland-relevant macro context only when tied to Finnish market impact.",
                "- Do not center the recap on broad Europe-only or global-only narratives without a clear Finland/Helsinki link.",
                "Required Finland coverage for the session:",
                "- OMX Helsinki index move during the session (direction + key driver).",
                "- intra-day sector rotation/leadership inside Finnish equities.",
                "- macro/policy context that explicitly connects to Finnish market impact in this session.",
                "- one concrete company-level driver (e.g., Nokia, Wartsila, Kone, Neste, Fortum, Sampo, Nordea, etc.).",
                "Return JSON wrapped in [RECAP_JSON]...[/RECAP_JSON].",
            ]
        else:
            lines = [
                "Generate a weekly Finland market recap JSON.",
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
                "- capture the week-wide trajectory and vibe (early/mid/late-week where evidence exists), not just a single-day snapshot.",
                "- Prioritize OMX Helsinki and Finnish listed equities.",
                "- Include Finland-relevant macro context only when tied to Finnish market impact.",
                "- Do not center the recap on broad Europe-only or global-only narratives without a clear Finland/Helsinki link.",
                "Required Finland coverage:",
                "- OMX Helsinki index trajectory across the week (direction + key driver).",
                "- sector rotation/leadership inside Finnish equities.",
                "- macro/policy context that explicitly connects to Finnish market impact.",
                "- one concrete company-level driver (e.g., Nokia, Wartsila, Kone, Neste, Fortum, Sampo, Nordea, etc.).",
                "Return JSON wrapped in [RECAP_JSON]...[/RECAP_JSON].",
            ]
    else:
        if is_daily:
            lines = [
                f"Generate a daily {market_label} market recap JSON for a single trading session.",
                f"Session date: {period_start.isoformat()}",
                "Use ONLY this schema:",
                '{"summary":"string","bullets":[{"text":"string","source_indices":[0]}]}',
                "Rules:",
                "- summary must be non-empty plain text.",
                "- bullets must contain 3-6 items.",
                "- each bullet must include at least one integer index in source_indices.",
                "- source_indices must reference only provided Source [i] blocks.",
                "- do not emit keys outside summary/bullets/text/source_indices.",
                "- avoid generic global-market-only narrative; ground claims in provided market-specific sources.",
                "- focus strictly on the single trading session (open, intra-day moves, close), not multi-day arcs.",
                "Return JSON wrapped in [RECAP_JSON]...[/RECAP_JSON].",
            ]
        else:
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
                "- capture the week-wide trajectory and vibe (early/mid/late-week where evidence exists), not just a single-day snapshot.",
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
    market: str = "US",
    cadence: str = "weekly",
    period_start: date,
    period_end: date,
    agent: MultiAgent | None = None,
) -> GeneratorResult:
    llm = agent or MultiAgent()
    prompt = _build_prompt(retrieval, market, period_start, period_end, cadence)
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
