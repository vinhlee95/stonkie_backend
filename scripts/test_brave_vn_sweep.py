"""Brave VN request-shape sweep.

Compares country/search_lang/count combinations on a fixed VN query/window.
Uses the expanded VN allowlist (Phase 6.5 design) and a multi-suffix-aware
registrable-domain helper so .com.vn / .gov.vn match correctly.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv

EXPANDED_VN_ALLOWLIST = {
    # existing
    "cafef.vn",
    "vietstock.vn",
    "vneconomy.vn",
    "vnexpress.net",
    "tinnhanhchungkhoan.vn",
    "vietnamfinance.vn",
    "simplize.vn",
    "fireant.vn",
    "stockbiz.vn",
    "investing.com",
    "reuters.com",
    # added
    "nhandan.vn",
    "vietnamplus.vn",
    "dnse.com.vn",
    "tienphong.vn",
    "hsx.vn",
    "hnx.vn",
    "ssc.gov.vn",
    "sbv.gov.vn",
    "baodautu.vn",
    "thoibaotaichinhvietnam.vn",
    "vir.com.vn",
    "bnews.vn",
    "thanhnien.vn",
    "tuoitre.vn",
    "doanhnhansaigon.vn",
    "ssi.com.vn",
    "vndirect.com.vn",
    "mbs.com.vn",
    "hsc.com.vn",
}

MULTI_PART_VN_SUFFIXES = (".com.vn", ".gov.vn", ".org.vn", ".net.vn", ".edu.vn")


def registrable_domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return ""
    for suffix in MULTI_PART_VN_SUFFIXES:
        if host.endswith(suffix):
            base = host[: -len(suffix)]
            label = base.split(".")[-1] if base else ""
            return f"{label}{suffix}" if label else suffix.lstrip(".")
    labels = host.split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def brave_search(api_key: str, *, query: str, country: str, lang: str, count: int, freshness: str) -> dict:
    resp = httpx.get(
        "https://api.search.brave.com/res/v1/llm/context",
        headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
        params={"q": query, "country": country, "search_lang": lang, "count": count, "freshness": freshness},
        timeout=20.0,
    )
    if resp.status_code >= 400:
        return {"_error": f"{resp.status_code}: {resp.text[:200]}"}
    return resp.json()


def evaluate(data: dict) -> dict:
    if "_error" in data:
        return {"error": data["_error"]}
    hits = (data.get("grounding") or {}).get("generic", []) or []
    domains = [registrable_domain(h.get("url", "")) for h in hits]
    in_allow = sum(1 for d in domains if d in EXPANDED_VN_ALLOWLIST)
    return {
        "count": len(hits),
        "allowlisted": in_allow,
        "allowlist_rate": (in_allow / len(hits)) if hits else 0.0,
        "domains": domains,
    }


def main() -> None:
    load_dotenv()
    api_key = os.environ.get("BRAVE_API_KEY")
    if not api_key:
        sys.exit("BRAVE_API_KEY not set")

    today = date.today()
    last_friday = today - timedelta(days=(today.weekday() - 4) % 7 or 7)
    last_monday = last_friday - timedelta(days=4)
    freshness = f"{last_monday.isoformat()}to{last_friday.isoformat()}"
    query = "thị trường chứng khoán Việt Nam tuần qua"

    combos = [
        ("ALL", "vi", 20),
        ("ALL", "vi", 30),
        ("ALL", "vi", 50),
        ("ALL", "en", 20),
        ("HK", "vi", 20),
        ("US", "vi", 20),
        ("JP", "vi", 20),
    ]

    results = []
    for country, lang, count in combos:
        data = brave_search(api_key, query=query, country=country, lang=lang, count=count, freshness=freshness)
        ev = evaluate(data)
        results.append({"country": country, "lang": lang, "count": count, **ev})

    print(f"query: {query!r}")
    print(f"window: {last_monday}..{last_friday}")
    print(f"allowlist size: {len(EXPANDED_VN_ALLOWLIST)}\n")
    print(f"{'country':<8} {'lang':<5} {'req':>4}  {'got':>4}  {'in_allow':>10}  {'rate':>6}  domains")
    for r in results:
        if "error" in r:
            print(f"{r['country']:<8} {r['lang']:<5} {r['count']:>4}  ERROR  {r['error']}")
            continue
    print()
    print(f"{'country':<8} {'lang':<5} {'req':>4}  {'got':>4}  {'allow':>5}  {'rate':>6}")
    for r in results:
        if "error" in r:
            print(f"{r['country']:<8} {r['lang']:<5} {r['count']:>4}  ERR")
            continue
        print(
            f"{r['country']:<8} {r['lang']:<5} {r['count']:>4}  {r['count']:>4}  {r['allowlisted']:>5}  {r['allowlist_rate']:>6.0%}"
        )
    # detailed per-combo domain dumps
    print("\n--- per-combo domains ---")
    for r in results:
        if "error" in r:
            continue
        print(f"\n[{r['country']}/{r['lang']}/{r['count']}] {r['allowlisted']}/{r['count']} allowlisted")
        for d in r["domains"]:
            mark = "✓" if d in EXPANDED_VN_ALLOWLIST else "✗"
            print(f"  {mark} {d}")

    out = Path(__file__).parent / "brave_vn_sweep.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nfull JSON: {out}")


if __name__ == "__main__":
    main()
