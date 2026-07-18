"""LLM connector that turns written recap text into a speakable podcast script.

Separate from `TtsConnector` because it is a different upstream call (chat
completions, not audio) and will likely need a different model as quality is
hardened. Services inject this; tests pass a fake.
"""

from __future__ import annotations

import logging

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

REWRITE_MODEL = "gpt-4o-mini"

_EN_PROMPT = """Rewrite this market recap as a script to be read aloud by a podcast host.

Rules:
- Plain spoken prose only. No headings, no markdown, no bullet points, no citations.
- Expand tickers and abbreviations: "NVDA" -> "Nvidia", "S&P 500" -> "the S and P five hundred".
- Speak numbers naturally: "-2.21%" -> "down two point two one percent".
- Keep EVERY figure exactly as given. Never round, never approximate, never invent a number.
- Cover every key point below. Do not drop any.
- Open with a one-line hook, close with a brief sign-off.
- Output only the script text.

Recap period: {period}
Summary: {summary}

Key points:
{bullets}"""

_VI_PROMPT = """Viết lại bản tin thị trường này thành kịch bản podcast tiếng Việt để đọc thành tiếng.

Quy tắc:
- Chỉ dùng văn nói tự nhiên. Không tiêu đề, không markdown, không gạch đầu dòng, không trích dẫn nguồn.
- Viết SỐ THÀNH CHỮ theo cách đọc tiếng Việt. Dấu chấm là phân cách hàng nghìn, dấu phẩy là thập phân:
  "1.787,45 điểm" -> "một nghìn bảy trăm tám mươi bảy phẩy bốn lăm điểm"
  "-2,24%" -> "giảm hai phẩy hai bốn phần trăm"
- GIỮ NGUYÊN mọi con số. Không làm tròn, không ước lượng, không tự bịa ra số mới.
- Mã cổ phiếu đọc từng chữ cái theo tên tiếng Việt: "SSI" -> "ét ét i", "TCB" -> "tê xê bê".
- "VN-Index" -> "vê en in-đéc". "Fed" -> "Cục Dự trữ Liên bang Mỹ". "USD" -> "đô la Mỹ".
- Phải nhắc đủ tất cả các điểm chính bên dưới. Không bỏ sót điểm nào.
- Mở đầu bằng một câu dẫn ngắn, kết thúc bằng lời chào ngắn.
- Chỉ xuất ra nội dung kịch bản.

Kỳ báo cáo: {period}
Tóm tắt: {summary}

Các điểm chính:
{bullets}"""

_PROMPTS = {"en": _EN_PROMPT, "vi": _VI_PROMPT}


class ScriptWriterConnector:
    """Rewrites recap text into speech-ready prose. Owns its SDK client."""

    def __init__(self, client: AsyncOpenAI | None = None, *, model: str = REWRITE_MODEL) -> None:
        self._client = client or AsyncOpenAI()
        self._model = model

    async def write(self, *, period: str, summary: str, bullets: list[str], language: str = "en") -> str:
        template = _PROMPTS.get(language, _EN_PROMPT)
        prompt = template.format(
            period=period,
            summary=summary,
            bullets="\n".join(f"- {b}" for b in bullets),
        )
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
        )
        return (response.choices[0].message.content or "").strip()
