"""
Latency probe for OpenRouter (and optional direct providers).

Measures:
- Time to first token (TTFT)
- Total stream duration

Usage:
  OPENROUTER_API_KEY=... python scripts/openrouter_latency_check.py \
    --prompt "Analyze Nvidia's competitive moat vs AMD and Intel..." \
    --model openrouter/google/gemini-2.0-flash-001

Optional direct comparisons (if keys present):
  OPENAI_API_KEY=... python scripts/openrouter_latency_check.py --provider openai
  GEMINI_API_KEY=... python scripts/openrouter_latency_check.py --provider gemini
"""

import argparse
import os
import time
from typing import Iterable, Tuple

from openai import OpenAI

try:
    from google import genai
    from google.genai import types as genai_types
except ImportError:
    genai = None
    genai_types = None


DEFAULT_PROMPT = (
    "You are an expert business analyst. Analyze this company question with the same depth as "
    "CompanyGeneralHandler in the app:\n\n"
    "Company: NVIDIA (ticker: NVDA)\n"
    "Question: How exposed is NVIDIA to a slowdown in AI datacenter build-outs over the next 12 months, "
    "and what offsets (software mix, pricing power, backlog) can protect gross margins?\n\n"
    "Instructions:\n"
    "- Keep the response under 200 words, dense with insight, no filler.\n"
    "- Structure with short paragraphs and bullet points where helpful; avoid markdown headers.\n"
    "- Provide 3 concrete risks with rough likelihoods and 3 catalysts (positive/negative) tied to timelines.\n"
    "- Cite likely data sources at the end (e.g., earnings calls, supply chain checks) without URLs."
)


def measure_latency(parts: Iterable[str]) -> Tuple[float, float]:
    """Return TTFT and total duration (seconds)."""
    t_start = time.perf_counter()
    ttft = None
    for chunk in parts:
        if ttft is None:
            ttft = time.perf_counter() - t_start
    t_total = time.perf_counter() - t_start
    return (ttft or 0.0, t_total)


def stream_openrouter(prompt: str, model: str) -> Iterable[str]:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required for OpenRouter provider")

    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    client = OpenAI(api_key=api_key, base_url=base_url)

    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        stream=True,
        max_tokens=512,
    )
    for event in resp:
        delta = event.choices[0].delta.content
        if delta:
            yield delta


def stream_openai(prompt: str, model: str) -> Iterable[str]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for openai provider")

    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        stream=True,
    )
    for event in resp:
        delta = event.choices[0].delta.content
        if delta:
            yield delta


def stream_gemini(prompt: str, model: str) -> Iterable[str]:
    if genai is None:
        raise RuntimeError("google-genai is not installed; cannot run gemini provider")

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required for gemini provider")

    client = genai.Client(api_key=api_key)
    chat = client.chats.create(model=model)
    response = chat.send_message_stream(prompt)
    for chunk in response:
        for candidate in chunk.candidates:
            for part in candidate.content.parts:
                text = getattr(part, "text", None)
                if text:
                    yield text


def main():
    parser = argparse.ArgumentParser(description="Measure TTFT and total latency for a prompt.")
    parser.add_argument("--prompt", type=str, default=DEFAULT_PROMPT, help="Prompt to send")
    parser.add_argument(
        "--provider",
        type=str,
        default="openrouter",
        choices=["openrouter", "openai", "gemini"],
        help="Provider to test",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=os.getenv("OPENROUTER_MODEL", "openrouter/google/gemini-2.0-flash-001"),
        help="Model name to use",
    )
    args = parser.parse_args()

    if args.provider == "openrouter":
        streamer = stream_openrouter
    elif args.provider == "openai":
        streamer = stream_openai
    else:
        streamer = stream_gemini

    print(f"Running provider={args.provider}, model={args.model}")
    ttft, total = measure_latency(streamer(args.prompt, args.model))
    print(f"TTFT: {ttft:.3f}s, Total: {total:.3f}s")


if __name__ == "__main__":
    main()
