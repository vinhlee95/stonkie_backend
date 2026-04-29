"""Aggregate eval run artifacts into latency/source-quality stats + per-prompt comparison."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit


def is_opaque_redirect(url: str) -> bool:
    return "vertexaisearch.cloud.google.com/grounding-api-redirect" in (url or "")


def domain_of(url: str) -> str:
    if is_opaque_redirect(url):
        return "(opaque-redirect)"
    return (urlsplit(url).hostname or "").lower().lstrip("www.")


def load_arm(prompt_dir: Path, arm: str) -> dict | None:
    path = prompt_dir / f"{arm}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir")
    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir)

    spec = json.loads((run_dir / "prompts.json").read_text())
    prompts = spec["prompts"]

    per_arm_latency = defaultdict(list)
    per_arm_total_latency = defaultdict(list)
    per_arm_citation_count = defaultdict(list)
    per_arm_answer_chars = defaultdict(list)
    per_arm_domain_counter: dict[str, Counter] = {"online": Counter(), "brave": Counter()}
    per_arm_opaque = defaultdict(int)
    per_arm_total_cites = defaultdict(int)

    rows = []

    for prompt in prompts:
        prompt_dir = run_dir / prompt["id"]
        row = {"id": prompt["id"], "category": prompt["category"]}
        for arm in ("online", "brave"):
            data = load_arm(prompt_dir, arm)
            if data is None:
                continue
            ret = data.get("retrieval_seconds") or 0.0
            gen = data.get("generation_seconds") or 0.0
            total = ret + gen
            per_arm_latency[arm].append(gen)
            per_arm_total_latency[arm].append(total)
            cites = data.get("citations") or []
            per_arm_citation_count[arm].append(len(cites))
            per_arm_answer_chars[arm].append(len(data.get("answer") or ""))
            for c in cites:
                d = domain_of(c.get("url", ""))
                per_arm_domain_counter[arm][d] += 1
                per_arm_total_cites[arm] += 1
                if is_opaque_redirect(c.get("url", "")):
                    per_arm_opaque[arm] += 1
            row[f"{arm}_total_s"] = round(total, 2)
            row[f"{arm}_cites"] = len(cites)
            row[f"{arm}_chars"] = len(data.get("answer") or "")
        rows.append(row)

    def pct(values, p):
        if not values:
            return None
        s = sorted(values)
        k = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
        return round(s[k], 2)

    print(f"Run: {run_dir}")
    print(f"Prompts: {len(prompts)}\n")

    print("=" * 70)
    print("LATENCY (seconds, generation only / total incl. retrieval)")
    print("=" * 70)
    for arm in ("online", "brave"):
        gens = per_arm_latency[arm]
        tots = per_arm_total_latency[arm]
        if not gens:
            continue
        print(
            f"  {arm:6}  gen p50={pct(gens, 50)}  p95={pct(gens, 95)}  "
            f"mean={round(statistics.mean(gens), 2)}  ||  "
            f"total p50={pct(tots, 50)}  p95={pct(tots, 95)}  mean={round(statistics.mean(tots), 2)}"
        )

    print("\n" + "=" * 70)
    print("CITATIONS")
    print("=" * 70)
    for arm in ("online", "brave"):
        cs = per_arm_citation_count[arm]
        if not cs:
            continue
        opaque = per_arm_opaque[arm]
        total = per_arm_total_cites[arm]
        opaque_pct = round(100 * opaque / total, 1) if total else 0.0
        print(
            f"  {arm:6}  per-prompt mean={round(statistics.mean(cs), 1)}  "
            f"min={min(cs)}  max={max(cs)}  total={total}  "
            f"opaque-redirect-rate={opaque_pct}%"
        )

    print("\n" + "=" * 70)
    print("ANSWER LENGTH (chars)")
    print("=" * 70)
    for arm in ("online", "brave"):
        chars = per_arm_answer_chars[arm]
        if not chars:
            continue
        print(f"  {arm:6}  mean={round(statistics.mean(chars))}  min={min(chars)}  max={max(chars)}")

    print("\n" + "=" * 70)
    print("TOP DOMAINS (per arm, top 10)")
    print("=" * 70)
    for arm in ("online", "brave"):
        print(f"  [{arm}]")
        for dom, n in per_arm_domain_counter[arm].most_common(10):
            print(f"    {n:3}  {dom}")

    print("\n" + "=" * 70)
    print("PER-PROMPT TIMING + CITATION COUNT")
    print("=" * 70)
    print(f"  {'id':10} {'category':24}  {'online_s':>8} {'on_cites':>8}  {'brave_s':>8} {'br_cites':>8}")
    for r in rows:
        print(
            f"  {r['id']:10} {r['category']:24}  "
            f"{r.get('online_total_s', '-'):>8} {r.get('online_cites', '-'):>8}  "
            f"{r.get('brave_total_s', '-'):>8} {r.get('brave_cites', '-'):>8}"
        )


if __name__ == "__main__":
    main()
