"""Compare retrieval strategies for /analyze.

Arms:
- online        : OpenRouter :online (in-process, no backend needed)
- brave         : Brave search top-K=5 → stuff (in-process, no backend needed)
- v1-endpoint   : POST live backend /api/companies/{ticker}/analyze (SSE)
- v2-endpoint   : POST live backend /api/v2/companies/{ticker}/analyze (SSE)

Endpoint arms hit a running backend (default http://localhost:8080) and parse
the JSON-lines SSE stream that both v1 and v2 emit (each event is
`json.dumps({type, body}) + "\\n\\n"`).

Usage:
    source venv/bin/activate
    PYTHONPATH=. python scripts/eval_analyze_search/run_eval.py
    PYTHONPATH=. python scripts/eval_analyze_search/run_eval.py --only csf-01,news-01
    PYTHONPATH=. python scripts/eval_analyze_search/run_eval.py --arm v1-endpoint,v2-endpoint
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


# ---------- Endpoint arms (live backend) ----------


ENDPOINT_TIMEOUT = httpx.Timeout(connect=5.0, read=180.0, write=10.0, pool=5.0)


def _iter_endpoint_events(response: httpx.Response):
    """Parse v1/v2 stream: each event is `json.dumps(...) + '\\n\\n'`. Robust to chunking."""
    buffer = ""
    for chunk in response.iter_text():
        if not chunk:
            continue
        buffer += chunk
        while "\n\n" in buffer:
            raw, buffer = buffer.split("\n\n", 1)
            raw = raw.strip()
            if not raw:
                continue
            # Some SSE-style implementations may prefix with "data: "; strip if present.
            if raw.startswith("data:"):
                raw = raw[5:].lstrip()
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                yield {"type": "_parse_error", "body": raw[:200]}
    tail = buffer.strip()
    if tail:
        if tail.startswith("data:"):
            tail = tail[5:].lstrip()
        try:
            yield json.loads(tail)
        except json.JSONDecodeError:
            yield {"type": "_parse_error", "body": tail[:200]}


def _normalize_ticker_for_path(prompt: dict) -> str:
    return (prompt.get("ticker") or "NONE").strip().upper() or "NONE"


def _flatten_sources_payload(body) -> list[dict]:
    """v2 sends {sources:[...]}; v1 may send list or dict variants."""
    if isinstance(body, dict):
        if isinstance(body.get("sources"), list):
            return [s for s in body["sources"] if isinstance(s, dict)]
        # sources_grouped style: {group_name: [sources]}
        flat: list[dict] = []
        for v in body.values():
            if isinstance(v, list):
                flat.extend(s for s in v if isinstance(s, dict))
        return flat
    if isinstance(body, list):
        return [s for s in body if isinstance(s, dict)]
    return []


def run_endpoint_arm(prompt: dict, *, arm: str, base_url: str, preferred_model: str) -> dict:
    ticker = _normalize_ticker_for_path(prompt)
    path = "/api/companies" if arm == "v1-endpoint" else "/api/v2/companies"
    url = f"{base_url.rstrip('/')}{path}/{ticker}/analyze"
    payload = {
        "question": prompt["text"],
        "useUrlContext": False,
        "deepAnalysis": False,
        "preferredModel": preferred_model,
        "disableCache": True,
        "debugPromptContext": True,
    }

    event_types: dict[str, int] = {}
    answer_parts: list[str] = []
    sources: list[dict] = []
    thinking_msgs: list[str] = []
    related: list[str] = []
    model_used: str | None = None
    request_id: str | None = None
    error_event: dict | None = None
    debug_prompt_context_payload: dict | None = None
    first_event_at: float | None = None
    first_answer_at: float | None = None
    first_source_at: float | None = None

    error: str | None = None
    t0 = time.monotonic()
    try:
        with httpx.stream(
            "POST",
            url,
            json=payload,
            timeout=ENDPOINT_TIMEOUT,
            headers={"accept": "text/event-stream"},
        ) as response:
            if response.status_code >= 400:
                # Drain body for diagnostics.
                try:
                    body_text = "".join(response.iter_text())
                except Exception:
                    body_text = ""
                error = f"HTTP {response.status_code}: {body_text[:300]}"
            else:
                for ev in _iter_endpoint_events(response):
                    et = ev.get("type", "_unknown") if isinstance(ev, dict) else "_unknown"
                    event_types[et] = event_types.get(et, 0) + 1
                    body = ev.get("body") if isinstance(ev, dict) else None
                    now = time.monotonic() - t0
                    if first_event_at is None:
                        first_event_at = now
                    if et == "answer" and isinstance(body, str):
                        if first_answer_at is None:
                            first_answer_at = now
                        answer_parts.append(body)
                    elif et in ("sources", "sources_grouped"):
                        flat = _flatten_sources_payload(body)
                        if flat and first_source_at is None:
                            first_source_at = now
                        sources.extend(flat)
                    elif et == "thinking_status" and isinstance(body, str):
                        thinking_msgs.append(body)
                    elif et == "model_used" and isinstance(body, str):
                        model_used = body
                    elif et == "debug_prompt_context" and isinstance(body, dict):
                        debug_prompt_context_payload = body
                    elif et == "related_question" and isinstance(body, str):
                        related.append(body)
                    elif et == "meta" and isinstance(body, dict):
                        rid = body.get("request_id") or body.get("requestId")
                        if isinstance(rid, str):
                            request_id = rid
                    elif et == "error":
                        error_event = ev
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    total = round(time.monotonic() - t0, 3)
    answer = "".join(answer_parts)

    citations = [
        {
            "source_id": s.get("source_id") or s.get("id"),
            "url": s.get("url"),
            "title": s.get("title"),
            "publisher": s.get("publisher"),
            "published_at": s.get("published_at"),
            "is_trusted": s.get("is_trusted"),
        }
        for s in sources
    ]

    return {
        "arm": arm,
        "prompt_id": prompt["id"],
        "url": url,
        "model": model_used,
        "preferred_model": preferred_model,
        "request_id": request_id,
        "answer": answer,
        "citations": citations,
        "thinking_status": thinking_msgs,
        "related_questions": related,
        "event_type_counts": event_types,
        "ttfb_seconds": round(first_event_at, 3) if first_event_at is not None else None,
        "first_answer_seconds": round(first_answer_at, 3) if first_answer_at is not None else None,
        "first_source_seconds": round(first_source_at, 3) if first_source_at is not None else None,
        "retrieval_seconds": None,
        "generation_seconds": total,
        "error_event": error_event,
        "debug_prompt_context": debug_prompt_context_payload,
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
    parser.add_argument(
        "--arm",
        default="both",
        help=(
            "Comma-separated arms: online,brave,v1-endpoint,v2-endpoint. "
            "Aliases: 'both'=online,brave; 'endpoints'=v1-endpoint,v2-endpoint; "
            "'all'=all four."
        ),
    )
    parser.add_argument("--run-id", help="Override run id (default: timestamp)")
    parser.add_argument(
        "--base-url",
        default=os.getenv("ANALYZE_EVAL_BASE_URL", "http://localhost:8080"),
        help="Backend base URL for endpoint arms (default: %(default)s)",
    )
    parser.add_argument(
        "--preferred-model",
        default="fastest",
        help="preferredModel sent to /analyze endpoints (default: fastest)",
    )
    args = parser.parse_args(argv)

    arm_aliases = {
        "both": ["online", "brave"],
        "endpoints": ["v1-endpoint", "v2-endpoint"],
        "all": ["online", "brave", "v1-endpoint", "v2-endpoint"],
    }
    raw_arms = arm_aliases.get(args.arm)
    if raw_arms is None:
        raw_arms = [a.strip() for a in args.arm.split(",") if a.strip()]
    valid = {"online", "brave", "v1-endpoint", "v2-endpoint"}
    invalid = [a for a in raw_arms if a not in valid]
    if invalid:
        raise SystemExit(f"Invalid arm(s): {invalid}. Valid: {sorted(valid)}")
    arms = raw_arms

    brave_key = os.getenv("BRAVE_API_KEY")
    if "brave" in arms and not brave_key:
        raise SystemExit("BRAVE_API_KEY missing")
    needs_openrouter = bool({"online", "brave"} & set(arms))
    if needs_openrouter and not os.getenv("OPENROUTER_API_KEY"):
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
    print(f"Prompts: {len(prompts)}  Arms: {arms}  Model: {DEFAULT_MODEL.value}")

    write_json(out_root / "prompts.json", spec)

    client = OpenRouterClient(model_name=DEFAULT_MODEL) if needs_openrouter else None

    summary_rows: list[dict] = []

    for prompt in prompts:
        prompt_dir = out_root / prompt["id"]
        print(f"\n[{prompt['id']}] {prompt['category']}: {prompt['text'][:80]}...")

        if "online" in arms:
            assert client is not None
            print("  -> online ...", end=" ", flush=True)
            result = run_online_arm(prompt, client=client)
            write_json(prompt_dir / "online.json", result)
            print(
                f"done ({result['generation_seconds']}s, {len(result['citations'])} citations, err={result['error']})"
            )
            summary_rows.append(_row(prompt, result))

        if "brave" in arms:
            assert client is not None and brave_key is not None
            print("  -> brave  ...", end=" ", flush=True)
            result = run_brave_arm(prompt, client=client, brave_api_key=brave_key)
            write_json(prompt_dir / "brave.json", result)
            print(
                f"done (ret={result['retrieval_seconds']}s, gen={result['generation_seconds']}s, "
                f"{len(result['citations'])} citations, err={result['error']})"
            )
            summary_rows.append(_row(prompt, result))

        for endpoint_arm in ("v1-endpoint", "v2-endpoint"):
            if endpoint_arm not in arms:
                continue
            print(f"  -> {endpoint_arm:11} ...", end=" ", flush=True)
            result = run_endpoint_arm(
                prompt,
                arm=endpoint_arm,
                base_url=args.base_url,
                preferred_model=args.preferred_model,
            )
            filename = endpoint_arm.replace("-endpoint", "_endpoint") + ".json"
            write_json(prompt_dir / filename, result)
            print(
                f"done (total={result['generation_seconds']}s, ttfb={result['ttfb_seconds']}s, "
                f"{len(result['citations'])} sources, err={result['error']})"
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
