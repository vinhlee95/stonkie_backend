"""Compare Tavily vs Brave for US weekly market recap.

Throwaway harness. Runs both providers against the same US weekly window using
the existing retrieval -> generate -> validate pipeline, then writes artifacts
to tmp/recap_compare/<period>/{tavily,brave}/ for human/judge review.

Usage:
    source venv/bin/activate
    PYTHONPATH=. python scripts/compare_us_recap_providers.py
    PYTHONPATH=. python scripts/compare_us_recap_providers.py --period-start 2026-04-20 --period-end 2026-04-24
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from agent.multi_agent import MultiAgent
from scripts.run_market_recap import compute_latest_completed_week
from services.market_recap.brave_client import BraveClient
from services.market_recap.recap_generator import GeneratorError, generate_recap
from services.market_recap.retrieval import retrieve_candidates
from services.market_recap.source_policy import is_allowlisted
from services.market_recap.tavily_client import TavilyClient
from services.market_recap.validator import validate_recap


def _serialize(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if is_dataclass(value):
        return {k: _serialize(v) for k, v in asdict(value).items()}
    if hasattr(value, "model_dump"):
        return _serialize(value.model_dump())
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    return value


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_serialize(payload), indent=2, ensure_ascii=False))


def _retrieval_to_dict(retrieval) -> dict:
    return {
        "stats": _serialize(retrieval.stats),
        "candidates": [
            {
                "title": c.title,
                "url": c.url,
                "canonical_url": c.canonical_url,
                "source_id": c.source_id,
                "published_date": c.published_date.isoformat() if c.published_date else None,
                "score": c.score,
                "provider": c.provider,
                "allowlisted": is_allowlisted(c.url, market="US"),
                "raw_content_chars": len(c.raw_content or ""),
                "snippet": c.snippet,
            }
            for c in retrieval.candidates
        ],
    }


def _run_provider(
    label: str,
    *,
    out_dir: Path,
    agent: MultiAgent,
    period_start: date,
    period_end: date,
    search_provider,
    planned_queries,
) -> dict:
    print(f"\n[{label}] retrieving...")
    t0 = time.monotonic()
    retrieval = retrieve_candidates(
        market="US",
        period_start=period_start,
        period_end=period_end,
        search_provider=search_provider,
        planned_queries=planned_queries,
        top_k=10,
    )
    t_retrieval = time.monotonic() - t0
    print(f"[{label}] retrieval: {retrieval.stats}")

    _write_json(out_dir / "retrieval.json", _retrieval_to_dict(retrieval))

    if not retrieval.candidates:
        meta = {
            "provider": label,
            "status": "no_candidates",
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "retrieval_seconds": t_retrieval,
        }
        _write_json(out_dir / "meta.json", meta)
        return meta

    print(f"[{label}] generating...")
    t1 = time.monotonic()
    try:
        gen = generate_recap(
            retrieval,
            market="US",
            period_start=period_start,
            period_end=period_end,
            agent=agent,
        )
        t_gen = time.monotonic() - t1
        gen_status = "ok"
        recap_dict = {
            "summary": gen.payload.summary,
            "bullets": [
                {
                    "text": b.text,
                    "citations": [c.source_id for c in b.citations],
                }
                for b in gen.payload.bullets
            ],
            "sources": [
                {
                    "id": s.id,
                    "url": s.url,
                    "title": s.title,
                    "publisher": s.publisher,
                    "published_at": s.published_at.isoformat() if s.published_at else None,
                }
                for s in gen.payload.sources
            ],
            "model": gen.model,
        }
        _write_json(out_dir / "recap.json", recap_dict)
        (out_dir / "raw_model_output.txt").write_text(gen.raw_model_output)
        validation = validate_recap(
            gen.payload,
            period_start=period_start,
            period_end=period_end,
            market="US",
        )
        _write_json(
            out_dir / "validation.json",
            {"ok": validation.ok, "failures": validation.failures, "warnings": validation.warnings},
        )
        validation_summary = {"ok": validation.ok, "failures": validation.failures, "warnings": validation.warnings}
    except (GeneratorError, ValueError) as exc:
        t_gen = time.monotonic() - t1
        gen_status = f"generation_failed: {exc}"
        validation_summary = None

    meta = {
        "provider": label,
        "status": gen_status,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "retrieval_seconds": round(t_retrieval, 2),
        "generation_seconds": round(t_gen, 2),
        "retrieval_stats": _serialize(retrieval.stats),
        "validation": validation_summary,
    }
    _write_json(out_dir / "meta.json", meta)
    print(f"[{label}] done in {t_retrieval + t_gen:.1f}s — status={gen_status}")
    return meta


def _print_summary(metas: list[dict]) -> None:
    print("\n" + "=" * 60)
    print("COMPARISON SUMMARY")
    print("=" * 60)
    for m in metas:
        print(f"\n[{m['provider']}]")
        print(f"  status         : {m.get('status')}")
        print(f"  retrieval (s)  : {m.get('retrieval_seconds')}")
        print(f"  generation (s) : {m.get('generation_seconds')}")
        stats = m.get("retrieval_stats") or {}
        if stats:
            print(f"  queries_total  : {stats.get('queries_total')}")
            print(f"  results_total  : {stats.get('results_total')}")
            print(f"  with_raw_content: {stats.get('with_raw_content')}")
            print(f"  allowlisted    : {stats.get('allowlisted')}")
            print(f"  ranked_top_k   : {stats.get('ranked_top_k')}")
        v = m.get("validation")
        if v:
            print(f"  validation.ok  : {v.get('ok')}")
            print(f"  failures       : {v.get('failures')}")
            print(f"  warnings       : {v.get('warnings')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period-start")
    parser.add_argument("--period-end")
    parser.add_argument("--out-root", default="tmp/recap_compare")
    args = parser.parse_args(argv)

    if args.period_start and args.period_end:
        period_start = date.fromisoformat(args.period_start)
        period_end = date.fromisoformat(args.period_end)
    else:
        period_start, period_end = compute_latest_completed_week()

    period_label = f"{period_start.isoformat()}_{period_end.isoformat()}"
    out_root = Path(args.out_root) / period_label
    print(f"Period: {period_start} -> {period_end}")
    print(f"Artifacts: {out_root}")

    tavily_key = os.getenv("TAVILY_API_KEY")
    brave_key = os.getenv("BRAVE_API_KEY")
    if not tavily_key:
        raise SystemExit("TAVILY_API_KEY missing")
    if not brave_key:
        raise SystemExit("BRAVE_API_KEY missing")

    agent = MultiAgent()
    print(f"Shared LLM model: {agent.model_name}")

    metas: list[dict] = []

    metas.append(
        _run_provider(
            "tavily",
            out_dir=out_root / "tavily",
            agent=agent,
            period_start=period_start,
            period_end=period_end,
            search_provider=TavilyClient(api_key=tavily_key),
            planned_queries=None,  # default US planner: 7 queries
        )
    )

    metas.append(
        _run_provider(
            "brave",
            out_dir=out_root / "brave",
            agent=agent,
            period_start=period_start,
            period_end=period_end,
            search_provider=BraveClient(api_key=brave_key, market="US"),
            planned_queries=None,  # same US planner as tavily
        )
    )

    _write_json(
        out_root / "summary.json",
        {"generated_at": datetime.now(UTC).isoformat(), "providers": metas},
    )
    _print_summary(metas)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
