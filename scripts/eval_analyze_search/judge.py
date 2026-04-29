"""LLM-as-judge for the analyze-search eval.

For each prompt, asks Claude Sonnet 4.6 to rate two anonymized answers (A/B)
on relevance, helpfulness, apparent_accuracy, and source_quality (1-5 each),
and to declare an overall winner. Each prompt is judged TWICE with the order
swapped to control for position bias. Final scores are the average.

Usage:
    source venv/bin/activate
    set -a && source .env && set +a
    PYTHONPATH=. python scripts/eval_analyze_search/judge.py tmp/analyze_eval/full01
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from ai_models.model_name import ModelName
from ai_models.openrouter_client import OpenRouterClient

JUDGE_MODEL = ModelName.Sonnet46
MAX_ANSWER_CHARS = 6000  # truncate to keep judge prompts compact


JUDGE_PROMPT = """You are an impartial evaluator comparing two AI assistant answers to the same finance/markets question. Score each answer 1-5 on four axes, then pick an overall winner.

USER QUESTION:
{question}

CATEGORY: {category}
TICKER: {ticker}

ANSWER A:
{answer_a}

CITATIONS A:
{citations_a}

ANSWER B:
{answer_b}

CITATIONS B:
{citations_b}

Score each answer 1-5 on:
- relevance: how directly the answer addresses the question
- helpfulness: how useful for a retail investor making a decision
- apparent_accuracy: do claims look internally consistent and supported by the cited sources? (you can't verify external truth, just check coherence + citation alignment)
- source_quality: are cited URLs reputable, traceable, with publication dates? Opaque redirect URLs are LOW quality.

Then declare an overall winner: "A", "B", or "tie".

Respond ONLY with valid JSON in this exact shape, no preamble:
{{
  "a": {{"relevance": <int>, "helpfulness": <int>, "apparent_accuracy": <int>, "source_quality": <int>}},
  "b": {{"relevance": <int>, "helpfulness": <int>, "apparent_accuracy": <int>, "source_quality": <int>}},
  "winner": "A" | "B" | "tie",
  "reason": "<one short sentence>"
}}"""


def _truncate(text: str, n: int) -> str:
    if len(text) <= n:
        return text
    return text[:n] + f"\n...[truncated, {len(text) - n} chars omitted]"


def _format_citations(arm_data: dict) -> str:
    cites = arm_data.get("citations") or []
    if not cites:
        return "(none)"
    lines = []
    for i, c in enumerate(cites, start=1):
        url = c.get("url", "")
        title = c.get("title") or ""
        published = c.get("published_at") or ""
        lines.append(f"  [{i}] {title} | {url} | published={published}")
    return "\n".join(lines)


def _build_judge_prompt(prompt: dict, a_data: dict, b_data: dict) -> str:
    return JUDGE_PROMPT.format(
        question=prompt["text"],
        category=prompt["category"],
        ticker=prompt.get("ticker") or "n/a",
        answer_a=_truncate(a_data.get("answer") or "", MAX_ANSWER_CHARS),
        citations_a=_format_citations(a_data),
        answer_b=_truncate(b_data.get("answer") or "", MAX_ANSWER_CHARS),
        citations_b=_format_citations(b_data),
    )


def _parse_judge_response(text: str) -> dict | None:
    # Try direct parse, fall back to extracting JSON block
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _judge_once(client: OpenRouterClient, prompt_text: str) -> tuple[dict | None, str]:
    chunks: list[str] = []
    for chunk in client.stream_chat(prompt=prompt_text, use_google_search=False):
        if isinstance(chunk, str):
            chunks.append(chunk)
    raw = "".join(chunks)
    return _parse_judge_response(raw), raw


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir")
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    spec = json.loads((run_dir / "prompts.json").read_text())
    prompts = spec["prompts"]

    client = OpenRouterClient(model_name=JUDGE_MODEL)
    print(f"Judge model: {client.model_name}")
    print(f"Run: {run_dir}")
    print(f"Prompts: {len(prompts)}\n")

    judgements: list[dict] = []

    for prompt in prompts:
        pid = prompt["id"]
        prompt_dir = run_dir / pid
        try:
            online = json.loads((prompt_dir / "online.json").read_text())
            brave = json.loads((prompt_dir / "brave.json").read_text())
        except FileNotFoundError:
            print(f"[{pid}] missing artifacts, skipping")
            continue

        # Pass 1: A=online, B=brave
        prompt_text_1 = _build_judge_prompt(prompt, online, brave)
        verdict_1, raw_1 = _judge_once(client, prompt_text_1)

        # Pass 2: A=brave, B=online (swap)
        prompt_text_2 = _build_judge_prompt(prompt, brave, online)
        verdict_2, raw_2 = _judge_once(client, prompt_text_2)

        if verdict_1 is None or verdict_2 is None:
            print(f"[{pid}] judge parse failed; saving raw")
            judgements.append(
                {
                    "prompt_id": pid,
                    "category": prompt["category"],
                    "error": "parse_failed",
                    "raw_pass1": raw_1,
                    "raw_pass2": raw_2,
                }
            )
            continue

        # Pass 1: A=online, B=brave -> map a->online, b->brave
        # Pass 2: A=brave,  B=online -> map a->brave,  b->online
        online_pass1 = verdict_1.get("a", {})
        brave_pass1 = verdict_1.get("b", {})
        brave_pass2 = verdict_2.get("a", {})
        online_pass2 = verdict_2.get("b", {})

        def _avg(d1: dict, d2: dict) -> dict:
            keys = set(d1.keys()) | set(d2.keys())
            out = {}
            for k in keys:
                v1 = d1.get(k)
                v2 = d2.get(k)
                vals = [v for v in (v1, v2) if isinstance(v, (int, float))]
                if vals:
                    out[k] = round(sum(vals) / len(vals), 2)
            return out

        online_avg = _avg(online_pass1, online_pass2)
        brave_avg = _avg(brave_pass1, brave_pass2)

        winner_1 = verdict_1.get("winner")
        winner_2 = verdict_2.get("winner")
        # Resolve winners back to actual arm
        actual_winner_1 = "online" if winner_1 == "A" else "brave" if winner_1 == "B" else "tie"
        actual_winner_2 = "brave" if winner_2 == "A" else "online" if winner_2 == "B" else "tie"

        if actual_winner_1 == actual_winner_2:
            consensus = actual_winner_1
        else:
            consensus = "split"

        judgements.append(
            {
                "prompt_id": pid,
                "category": prompt["category"],
                "online_scores": online_avg,
                "brave_scores": brave_avg,
                "winner_pass1": actual_winner_1,
                "winner_pass2": actual_winner_2,
                "consensus": consensus,
                "reason_pass1": verdict_1.get("reason"),
                "reason_pass2": verdict_2.get("reason"),
            }
        )
        print(
            f"[{pid:8}] online={online_avg}  brave={brave_avg}  "
            f"w1={actual_winner_1} w2={actual_winner_2} consensus={consensus}"
        )

    out_path = run_dir / "judgements.json"
    out_path.write_text(json.dumps({"judge_model": client.model_name, "judgements": judgements}, indent=2))
    print(f"\nWrote {out_path}")

    # Aggregate
    print("\n" + "=" * 70)
    print("AGGREGATE SCORES (avg across prompts, 1-5)")
    print("=" * 70)
    axes = ["relevance", "helpfulness", "apparent_accuracy", "source_quality"]
    for arm_key in ("online_scores", "brave_scores"):
        sums = {a: [] for a in axes}
        for j in judgements:
            scores = j.get(arm_key) or {}
            for a in axes:
                if a in scores:
                    sums[a].append(scores[a])
        label = arm_key.replace("_scores", "")
        line = "  ".join(f"{a}={round(sum(v) / len(v), 2) if v else 'n/a'}" for a, v in sums.items())
        print(f"  {label:6}  {line}")

    print("\n" + "=" * 70)
    print("WINNER COUNTS (consensus only — both passes agreed)")
    print("=" * 70)
    from collections import Counter

    counts = Counter(j.get("consensus") for j in judgements if "consensus" in j)
    for k, v in counts.most_common():
        print(f"  {k}: {v}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
