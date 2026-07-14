import logging

from agent.multi_agent import MultiAgent
from ai_models.model_name import ModelName

logger = logging.getLogger(__name__)

NUM_QUESTIONS = 3


def _build_prompt(summary: str, bullets: list[dict], market: str) -> str:
    bullet_lines = "\n".join(f"- {b.get('text', '')}" for b in bullets)
    market_key = market.upper()

    if market_key == "VN":
        return (
            f"Dựa trên bản tóm tắt thị trường sau, tạo đúng {NUM_QUESTIONS} câu hỏi theo dõi ngắn gọn "
            "mà người đọc có thể muốn tìm hiểu thêm.\n\n"
            f"Tóm tắt: {summary}\n\n"
            f"Các điểm chính:\n{bullet_lines}\n\n"
            "Yêu cầu:\n"
            "- Mỗi câu hỏi phải ngắn gọn (dưới 80 ký tự)\n"
            "- Câu hỏi phải cụ thể, liên quan trực tiếp đến nội dung tóm tắt\n"
            "- Mỗi câu hỏi trên MỘT dòng riêng\n"
            "- KHÔNG đánh số hoặc thêm tiền tố\n"
            "- Viết bằng tiếng Việt\n"
        )

    return (
        f"Based on this market recap, generate exactly {NUM_QUESTIONS} short follow-up questions "
        "that a reader might want to explore further.\n\n"
        f"Summary: {summary}\n\n"
        f"Key points:\n{bullet_lines}\n\n"
        "Requirements:\n"
        "- Each question must be concise (under 80 characters)\n"
        "- Questions should be specific to the recap content\n"
        "- Put EACH question on its OWN LINE\n"
        "- Do NOT number the questions or add any prefixes\n"
    )


def generate_questions(
    *,
    summary: str,
    bullets: list[dict],
    market: str,
) -> list[str]:
    try:
        agent = MultiAgent(model_name=ModelName.Gemini35Flash)
        prompt = _build_prompt(summary, bullets, market)
        return list(agent.generate_content_by_lines(prompt, max_lines=NUM_QUESTIONS))
    except Exception:
        logger.warning("question generation failed", exc_info=True)
        return []
