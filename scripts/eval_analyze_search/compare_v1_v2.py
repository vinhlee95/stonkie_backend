"""Side-by-side comparison report for v1-endpoint vs v2-endpoint eval runs.

Reads per-prompt artifacts written by run_eval.py and produces:
  - latency table (total, ttfb, first_answer, first_source) per prompt
  - source quality: counts, domain overlap, trusted-rate (v2), unique-to-v2 / unique-to-v1
  - answer characteristics: length, [N] inline citation count, refusal/insufficient-data hits
  - error / event-shape diffs

Usage:
    PYTHONPATH=. python scripts/eval_analyze_search/compare_v1_v2.py tmp/analyze_eval/<run_id>
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from pathlib import Path
from urllib.parse import urlsplit

REFUSAL_PATTERNS = [
    r"i (?:am sorry|cannot|don'?t have)",
    r"do(?:es)? not contain",
    r"i (?:do not|don'?t) have access",
    r"unable to (?:find|provide|locate)",
    r"insufficient (?:data|information)",
]
REFUSAL_RE = re.compile("|".join(REFUSAL_PATTERNS), re.IGNORECASE)
CITATION_RE = re.compile(r"\[(\d+)\]")


def domain_of(url: str | None) -> str:
    if not url:
        return ""
    return (urlsplit(url).hostname or "").lower().lstrip("www.")


def load(prompt_dir: Path, name: str) -> dict | None:
    p = prompt_dir / name
    if not p.exists():
        return None
    return json.loads(p.read_text())


def pct(values, p):
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
    return round(s[k], 2)


def fmt(x, default="-"):
    return default if x is None else x


def summarize_arm(data: dict) -> dict:
    answer = data.get("answer") or ""
    cites = data.get("citations") or []
    inline_n = sorted({int(m.group(1)) for m in CITATION_RE.finditer(answer)})
    domains = [domain_of(c.get("url")) for c in cites]
    domains = [d for d in domains if d]
    trusted = sum(1 for c in cites if c.get("is_trusted"))
    refusal = bool(REFUSAL_RE.search(answer))
    return {
        "total_s": data.get("generation_seconds"),
        "ttfb_s": data.get("ttfb_seconds"),
        "first_answer_s": data.get("first_answer_seconds"),
        "first_source_s": data.get("first_source_seconds"),
        "answer_chars": len(answer),
        "n_sources": len(cites),
        "n_trusted": trusted,
        "domains": domains,
        "inline_n_count": len(inline_n),
        "max_inline_n": max(inline_n) if inline_n else 0,
        "out_of_range_n": sum(1 for n in inline_n if n > len(cites)),
        "refusal": refusal,
        "model": data.get("model"),
        "error": data.get("error"),
        "event_types": data.get("event_type_counts") or {},
        "thinking_count": len(data.get("thinking_status") or []),
        "related_count": len(data.get("related_questions") or []),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    args = ap.parse_args(argv)
    run_dir = Path(args.run_dir)
    spec = json.loads((run_dir / "prompts.json").read_text())
    prompts = spec["prompts"]

    rows = []
    v1_lat, v2_lat = [], []
    v1_ttfb, v2_ttfb = [], []
    v1_src, v2_src = [], []
    v1_chars, v2_chars = [], []
    v1_refusals, v2_refusals = 0, 0
    overlap_per_prompt = []
    out_of_range_v2 = 0

    for prompt in prompts:
        pdir = run_dir / prompt["id"]
        v1 = load(pdir, "v1_endpoint.json")
        v2 = load(pdir, "v2_endpoint.json")
        if not v1 and not v2:
            continue
        s1 = summarize_arm(v1) if v1 else None
        s2 = summarize_arm(v2) if v2 else None
        d1 = set(s1["domains"]) if s1 else set()
        d2 = set(s2["domains"]) if s2 else set()
        inter = d1 & d2
        only1 = d1 - d2
        only2 = d2 - d1

        rows.append(
            {
                "id": prompt["id"],
                "category": prompt["category"],
                "ticker": prompt.get("ticker"),
                "v1": s1,
                "v2": s2,
                "domain_overlap": sorted(inter),
                "domain_only_v1": sorted(only1),
                "domain_only_v2": sorted(only2),
            }
        )

        if s1 and s1["total_s"] is not None:
            v1_lat.append(s1["total_s"])
        if s2 and s2["total_s"] is not None:
            v2_lat.append(s2["total_s"])
        if s1 and s1["ttfb_s"] is not None:
            v1_ttfb.append(s1["ttfb_s"])
        if s2 and s2["ttfb_s"] is not None:
            v2_ttfb.append(s2["ttfb_s"])
        if s1:
            v1_src.append(s1["n_sources"])
            v1_chars.append(s1["answer_chars"])
            if s1["refusal"]:
                v1_refusals += 1
        if s2:
            v2_src.append(s2["n_sources"])
            v2_chars.append(s2["answer_chars"])
            if s2["refusal"]:
                v2_refusals += 1
            out_of_range_v2 += s2["out_of_range_n"]
        if s1 and s2 and (d1 or d2):
            denom = max(1, len(d1 | d2))
            overlap_per_prompt.append(round(100 * len(inter) / denom, 1))

    print(f"Run: {run_dir}")
    print(f"Prompts compared: {len(rows)}\n")

    print("=" * 92)
    print("LATENCY (seconds)")
    print("=" * 92)
    print(f"  {'arm':12} {'p50':>8} {'p95':>8} {'mean':>8}   {'ttfb p50':>10} {'ttfb p95':>10}")
    for arm, lat, ttfb in [("v1-endpoint", v1_lat, v1_ttfb), ("v2-endpoint", v2_lat, v2_ttfb)]:
        if not lat:
            continue
        print(
            f"  {arm:12} {pct(lat, 50):>8} {pct(lat, 95):>8} {round(statistics.mean(lat), 2):>8}   "
            f"{fmt(pct(ttfb, 50)):>10} {fmt(pct(ttfb, 95)):>10}"
        )
    if v1_lat and v2_lat:
        delta = round(pct(v2_lat, 50) - pct(v1_lat, 50), 2)
        ratio = round(100 * (pct(v2_lat, 50) / pct(v1_lat, 50) - 1), 1) if pct(v1_lat, 50) else None
        print(f"  Δ p50 (v2-v1): {delta}s  ({ratio}% vs v1)")

    print("\n" + "=" * 92)
    print("SOURCES")
    print("=" * 92)
    for arm, src in [("v1-endpoint", v1_src), ("v2-endpoint", v2_src)]:
        if not src:
            continue
        print(
            f"  {arm:12} mean={round(statistics.mean(src), 1)}  min={min(src)}  max={max(src)}  "
            f"total={sum(src)}  prompts_with_zero={sum(1 for n in src if n == 0)}"
        )
    if overlap_per_prompt:
        print(
            f"  domain overlap (Jaccard, per-prompt): mean={round(statistics.mean(overlap_per_prompt), 1)}%  "
            f"median={round(statistics.median(overlap_per_prompt), 1)}%"
        )
    if out_of_range_v2:
        print(f"  v2 inline-[N] out-of-range citations: {out_of_range_v2}")

    print("\n" + "=" * 92)
    print("ANSWER")
    print("=" * 92)
    print(
        f"  v1 chars mean={round(statistics.mean(v1_chars))}  refusals={v1_refusals}/{len(v1_chars)}"
        if v1_chars
        else "  v1: no data"
    )
    print(
        f"  v2 chars mean={round(statistics.mean(v2_chars))}  refusals={v2_refusals}/{len(v2_chars)}"
        if v2_chars
        else "  v2: no data"
    )

    print("\n" + "=" * 92)
    print("PER-PROMPT SIDE-BY-SIDE")
    print("=" * 92)
    print(
        f"  {'id':10} {'cat':24} {'v1 s':>5} {'v2 s':>5}  {'v1 src':>6} {'v2 src':>6}  "
        f"{'v1 chr':>6} {'v2 chr':>6}  {'overlap':>7}  {'refus(v1/v2)':>12}"
    )
    for r in rows:
        s1, s2 = r["v1"], r["v2"]
        v1_t = fmt(s1 and s1["total_s"])
        v2_t = fmt(s2 and s2["total_s"])
        v1_n = fmt(s1 and s1["n_sources"])
        v2_n = fmt(s2 and s2["n_sources"])
        v1_c = fmt(s1 and s1["answer_chars"])
        v2_c = fmt(s2 and s2["answer_chars"])
        d1 = set(s1["domains"]) if s1 else set()
        d2 = set(s2["domains"]) if s2 else set()
        ov = round(100 * len(d1 & d2) / max(1, len(d1 | d2)), 0) if (d1 or d2) else 0
        refus = f"{int(bool(s1 and s1['refusal']))}/{int(bool(s2 and s2['refusal']))}"
        print(
            f"  {r['id']:10} {r['category']:24} {v1_t:>5} {v2_t:>5}  {v1_n:>6} {v2_n:>6}  "
            f"{v1_c:>6} {v2_c:>6}  {ov:>6}%  {refus:>12}"
        )

    print("\n" + "=" * 92)
    print("DOMAIN DIFFS (top per-prompt)")
    print("=" * 92)
    for r in rows:
        if not (r["domain_only_v1"] or r["domain_only_v2"]):
            continue
        print(f"  [{r['id']}]")
        if r["domain_only_v1"]:
            print(f"    only v1: {', '.join(r['domain_only_v1'])}")
        if r["domain_only_v2"]:
            print(f"    only v2: {', '.join(r['domain_only_v2'])}")
        if r["domain_overlap"]:
            print(f"    shared : {', '.join(r['domain_overlap'])}")

    print("\n" + "=" * 92)
    print("EVENT-SHAPE / THINKING DELTAS")
    print("=" * 92)
    print(f"  {'id':10} {'v1 events':40} {'v2 events':40}")
    for r in rows:
        e1 = r["v1"]["event_types"] if r["v1"] else {}
        e2 = r["v2"]["event_types"] if r["v2"] else {}
        s1s = ",".join(f"{k}:{v}" for k, v in sorted(e1.items()))
        s2s = ",".join(f"{k}:{v}" for k, v in sorted(e2.items()))
        print(f"  {r['id']:10} {s1s:40} {s2s:40}")

    out = run_dir / "compare_v1_v2.json"
    out.write_text(json.dumps({"run_dir": str(run_dir), "rows": rows}, indent=2, ensure_ascii=False))
    print(f"\nWrote: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
