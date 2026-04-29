"""Compare retrieval strategies for /analyze: OpenRouter :online vs Brave→stuff.

Throwaway harness. For each prompt in prompts.json, runs both arms through the
same base model + same answer template, captures answer/citations/timings/tokens,
writes per-prompt artifacts under tmp/analyze_eval/<run_id>/.

Both arms use the prompt verbatim (no query rewriting), matching how :online
works today. Brave arm uses top-K=5 with full raw_content, no allowlist, no
freshness gate (covers /analyze's full evergreen + breaking-news surface).

Usage:
    source venv/bin/activate
    PYTHONPATH=. python scripts/eval_analyze_search/run_eval.py
    PYTHONPATH=. python scripts/eval_analyze_search/run_eval.py --only csf-01,news-01
    PYTHONPATH=. python scripts/eval_analyze_search/run_eval.py --arm brave
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ai_models.model_name import ModelName
from ai_models.openrouter_client import OpenRouterClient

PROMPTS_PATH = Path(__file__).parent / "prompts.json"
DEFAULT_MODEL = ModelName.Gemini30Flash  # match /analyze default
BRAVE_TOP_K = 5
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/llm/context"


# ---------- Brave: minimal, no goggles, no freshness ----------


def brave_search_raw(query: str, *, api_key: str, count: int = 20) -> dict:
    response = httpx.get(
        BRAVE_ENDPOINT,
        headers={"X-Subscription-Token": api_key},
        params={
            "q": query,
            "country": "US",
            "search_lang": "en",
            "count": count,
            "context_threshold_mode": "strict",
        },
        timeout=15.0,
    )
    response.raise_for_status()
    return response.json()


def extract_top_passages(brave_response: dict, top_k: int) -> list[dict]:
    """Pull the top-K grounded passages with url/title/snippets."""
    sources_meta = brave_response.get("sources", {})
    results_meta = {
        item.get("url"): {"title": item.get("title", ""), "description": item.get("description", "")}
        for item in brave_response.get("results", [])
        if isinstance(item.get("url"), str)
    }

    passages: list[dict] = []
    grounded = brave_response.get("grounding", {}).get("generic", []) or []
    for item in grounded:
        url = item.get("url")
        if not isinstance(url, str) or not url:
            continue
        snippets = item.get("snippets", []) or []
        snippets = [s for s in snippets if isinstance(s, str)]
        meta = results_meta.get(url, {})
        age_values = sources_meta.get(url, {}).get("age", []) if isinstance(sources_meta, dict) else []
        published_at = age_values[0] if isinstance(age_values, list) and age_values else None
        passages.append(
            {
                "url": url,
                "title": item.get("title") or meta.get("title", ""),
                "snippet": meta.get("description", ""),
                "raw_content": "\n\n".join(snippets),
                "published_at": published_at,
            }
        )
        if len(passages) >= top_k:
            break

    if passages:
        return passages

    # fallback: use plain results if no grounded payload
    for result in brave_response.get("results", []):
        url = result.get("url")
        if not isinstance(url, str) or not url:
            continue
        passages.append(
            {
                "url": url,
                "title": result.get("title", ""),
                "snippet": result.get("description", ""),
                "raw_content": "",
                "published_at": None,
            }
        )
        if len(passages) >= top_k:
            break
    return passages


# ---------- Prompt assembly ----------


ANSWER_TEMPLATE = """You are a financial research assistant. Answer the user's question using the provided web search context. Cite sources inline using [N] notation matching the numbered passages below.

User question:
{question}

Web search context:
{context}

Answer in clear, concise prose. Inline-cite specific facts. If the context doesn't cover something, say so rather than guessing."""


def build_brave_prompt(question: str, passages: list[dict]) -> str:
    blocks = []
    for i, p in enumerate(passages, start=1):
        body = p["raw_content"] or p["snippet"]
        blocks.append(f"[{i}] {p['title']}\nURL: {p['url']}\n{body}")
    context = "\n\n---\n\n".join(blocks) if blocks else "(no results)"
    return ANSWER_TEMPLATE.format(question=question, context=context)


# ---------- Arm runners ----------


def run_online_arm(prompt: dict, *, client: OpenRouterClient) -> dict:
    t0 = time.monotonic()
    text_parts: list[str] = []
    citations: list[dict] = []
    error = None
    try:
        for chunk in client.stream_chat(prompt=prompt["text"], use_google_search=True):
            if isinstance(chunk, str):
                text_parts.append(chunk)
            elif isinstance(chunk, dict) and chunk.get("type") == "url_citation":
                citations.append(
                    {
                        "url": chunk.get("url"),
                        "title": chunk.get("title"),
                        "content_chars": len(chunk.get("content") or ""),
                    }
                )
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    return {
        "arm": "online",
        "prompt_id": prompt["id"],
        "model": client.model_name,
        "answer": "".join(text_parts),
        "citations": citations,
        "retrieval_seconds": None,
        "generation_seconds": round(time.monotonic() - t0, 3),
        "error": error,
    }


def run_brave_arm(prompt: dict, *, client: OpenRouterClient, brave_api_key: str) -> dict:
    error = None
    passages: list[dict] = []
    raw_response: dict | None = None
    t_retrieval_start = time.monotonic()
    try:
        raw_response = brave_search_raw(prompt["text"], api_key=brave_api_key)
        passages = extract_top_passages(raw_response, top_k=BRAVE_TOP_K)
    except Exception as exc:
        error = f"brave: {type(exc).__name__}: {exc}"
    t_retrieval = round(time.monotonic() - t_retrieval_start, 3)

    if error:
        return {
            "arm": "brave",
            "prompt_id": prompt["id"],
            "model": client.model_name,
            "answer": "",
            "citations": [],
            "passages": [],
            "retrieval_seconds": t_retrieval,
            "generation_seconds": None,
            "error": error,
        }

    stuffed_prompt = build_brave_prompt(prompt["text"], passages)

    text_parts: list[str] = []
    t_gen_start = time.monotonic()
    try:
        for chunk in client.stream_chat(prompt=stuffed_prompt, use_google_search=False):
            if isinstance(chunk, str):
                text_parts.append(chunk)
    except Exception as exc:
        error = f"openrouter: {type(exc).__name__}: {exc}"
    t_gen = round(time.monotonic() - t_gen_start, 3)

    citations = [
        {
            "index": i,
            "url": p["url"],
            "title": p["title"],
            "published_at": p["published_at"],
            "content_chars": len(p["raw_content"] or ""),
        }
        for i, p in enumerate(passages, start=1)
    ]

    return {
        "arm": "brave",
        "prompt_id": prompt["id"],
        "model": client.model_name,
        "answer": "".join(text_parts),
        "citations": citations,
        "passages": passages,
        "brave_response_summary": {
            "results_count": len(raw_response.get("results", []) or []) if raw_response else 0,
            "grounding_count": len((raw_response.get("grounding", {}) or {}).get("generic", []) or [])
            if raw_response
            else 0,
        },
        "retrieval_seconds": t_retrieval,
        "generation_seconds": t_gen,
        "error": error,
    }


# ---------- Driver ----------


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-root", default="tmp/analyze_eval")
    parser.add_argument("--only", help="Comma-separated prompt ids to run (default: all)")
    parser.add_argument("--arm", choices=["online", "brave", "both"], default="both")
    parser.add_argument("--run-id", help="Override run id (default: timestamp)")
    args = parser.parse_args(argv)

    brave_key = os.getenv("BRAVE_API_KEY")
    if args.arm in ("brave", "both") and not brave_key:
        raise SystemExit("BRAVE_API_KEY missing")
    if not os.getenv("OPENROUTER_API_KEY"):
        raise SystemExit("OPENROUTER_API_KEY missing")

    spec = json.loads(PROMPTS_PATH.read_text())
    prompts = spec["prompts"]
    if args.only:
        wanted = {p.strip() for p in args.only.split(",") if p.strip()}
        prompts = [p for p in prompts if p["id"] in wanted]
        if not prompts:
            raise SystemExit(f"No prompts matched --only={args.only}")

    run_id = args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_root = Path(args.out_root) / run_id
    print(f"Run: {run_id}")
    print(f"Out: {out_root}")
    print(f"Prompts: {len(prompts)}  Arms: {args.arm}  Model: {DEFAULT_MODEL.value}")

    write_json(out_root / "prompts.json", spec)

    client = OpenRouterClient(model_name=DEFAULT_MODEL)

    summary_rows: list[dict] = []

    for prompt in prompts:
        prompt_dir = out_root / prompt["id"]
        print(f"\n[{prompt['id']}] {prompt['category']}: {prompt['text'][:80]}...")

        if args.arm in ("online", "both"):
            print("  -> online ...", end=" ", flush=True)
            result = run_online_arm(prompt, client=client)
            write_json(prompt_dir / "online.json", result)
            print(
                f"done ({result['generation_seconds']}s, {len(result['citations'])} citations, err={result['error']})"
            )
            summary_rows.append(_row(prompt, result))

        if args.arm in ("brave", "both"):
            print("  -> brave  ...", end=" ", flush=True)
            result = run_brave_arm(prompt, client=client, brave_api_key=brave_key)
            write_json(prompt_dir / "brave.json", result)
            print(
                f"done (ret={result['retrieval_seconds']}s, gen={result['generation_seconds']}s, "
                f"{len(result['citations'])} citations, err={result['error']})"
            )
            summary_rows.append(_row(prompt, result))

    write_json(out_root / "summary.json", {"run_id": run_id, "model": DEFAULT_MODEL.value, "rows": summary_rows})
    print(f"\nDone. Artifacts: {out_root}")
    return 0


def _row(prompt: dict, result: dict) -> dict:
    return {
        "prompt_id": prompt["id"],
        "category": prompt["category"],
        "arm": result["arm"],
        "retrieval_seconds": result.get("retrieval_seconds"),
        "generation_seconds": result.get("generation_seconds"),
        "citations": len(result.get("citations") or []),
        "answer_chars": len(result.get("answer") or ""),
        "error": result.get("error"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
