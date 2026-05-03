"""Tests for services.analyze_retrieval.source_policy (Phase 0)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from services.analyze_retrieval import source_policy as sp

# ---------------------------------------------------------------------------
# GLOBAL tier resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://reuters.com/article/x", 1),
        ("https://www.reuters.com/x", 1),
        ("https://markets.ft.com/x", 1),
        ("https://bloomberg.com/x", 1),
        ("https://sec.gov/x", 1),
        ("https://anything.gov/x", 1),
        ("https://sub.dept.gov/x", 1),
        ("https://companieshouse.gov.uk/x", 1),
        ("https://apnews.com/x", 1),
        ("https://www.nytimes.com/x", 1),
        ("https://investing.com/x", 2),
        ("https://www.nasdaq.com/x", 2),
        ("https://www.etf.com/x", 2),
        ("https://finance.yahoo.com/x", 2),
        ("https://random-blog.com/x", None),
    ],
)
def test_tier_for_global(url: str, expected: int | None) -> None:
    assert sp.tier_for(url, "GLOBAL") == expected


# ---------------------------------------------------------------------------
# VN tier resolution (no GLOBAL inheritance, no *.gov wildcard)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://cafef.vn/x", 1),
        ("https://www.hsx.vn/x", 1),
        ("https://hnx.vn/x", 1),
        ("https://ssc.gov.vn/x", 1),
        ("https://sbv.gov.vn/x", 1),
        ("https://nhandan.vn/x", 1),
        ("https://ssi.com.vn/x", 2),
        ("https://www.vietnamplus.vn/x", 2),
        ("https://www.dnse.com.vn/x", 2),
        ("https://www.vndirect.com.vn/x", 2),
        ("https://reuters.com/x", None),
        ("https://random-blog.vn/x", None),
        ("https://anything.gov/x", None),
    ],
)
def test_tier_for_vn(url: str, expected: int | None) -> None:
    assert sp.tier_for(url, "VN") == expected


# ---------------------------------------------------------------------------
# FI tier resolution (union by tier number with GLOBAL)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://inderes.fi/x", 1),
        ("https://kauppalehti.fi/x", 1),
        ("https://hs.fi/x", 2),
        ("https://yle.fi/x", 2),
        ("https://arvopaperi.fi/x", 2),
        ("https://reuters.com/x", 1),
        ("https://investing.com/x", 2),
        ("https://anything.gov/x", 1),
        ("https://random-blog.com/x", None),
    ],
)
def test_tier_for_fi(url: str, expected: int | None) -> None:
    assert sp.tier_for(url, "FI") == expected


# ---------------------------------------------------------------------------
# Discards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://reddit.com/r/x", True),
        ("https://www.reddit.com/r/x", True),
        ("https://x.com/some/path", True),
        ("https://twitter.com/abc", True),
        ("https://www.youtube.com/watch?v=abc", True),
        ("https://facebook.com/page", True),
        ("https://linkedin.com/in/abc", True),
        ("https://quora.com/q", True),
        ("https://medium.com/x", True),
        ("https://substack.com/x", True),
        ("https://www.tradingview.com/ideas/abc", True),
        ("https://www.tradingview.com/symbols/abc", False),
        # Non-/ideas tradingview pages are intentionally permissive (path-prefix discard only).
        ("https://tradingview.com/", False),
        ("https://reuters.com/x", False),
    ],
)
def test_is_discarded(url: str, expected: bool) -> None:
    assert sp.is_discarded(url) is expected


def test_discards_override_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even tier-1 sources are blocked if also in DISCARDS."""
    patched = sp.DISCARDS | {"reuters.com"}
    monkeypatch.setattr(sp, "DISCARDS", patched)
    assert sp.tier_for("https://reuters.com/x", "GLOBAL") is None
    assert sp.is_trusted("https://reuters.com/x", "GLOBAL") is False


def test_discarded_url_is_not_trusted_in_any_market() -> None:
    for market in ("GLOBAL", "VN", "FI"):
        assert sp.tier_for("https://medium.com/x", market) is None  # type: ignore[arg-type]
        assert sp.is_trusted("https://medium.com/x", market) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# is_trusted mirror
# ---------------------------------------------------------------------------


def test_is_trusted_mirrors_tier_for() -> None:
    assert sp.is_trusted("https://reuters.com/x", "GLOBAL") is True
    assert sp.is_trusted("https://investing.com/x", "GLOBAL") is True
    assert sp.is_trusted("https://random-blog.com/x", "GLOBAL") is False
    assert sp.is_trusted("https://reddit.com/x", "GLOBAL") is False
    assert sp.is_trusted("https://cafef.vn/x", "VN") is True
    assert sp.is_trusted("https://reuters.com/x", "VN") is False
    assert sp.is_trusted("https://inderes.fi/x", "FI") is True


# ---------------------------------------------------------------------------
# Tier non-overlap invariant
# ---------------------------------------------------------------------------


def test_tier_lists_do_not_overlap() -> None:
    assert sp.GLOBAL_TIER_1.isdisjoint(sp.GLOBAL_TIER_2)
    assert sp.FI_EXTENSION_TIER_1.isdisjoint(sp.FI_EXTENSION_TIER_2)
    assert sp.VN_TIER_1.isdisjoint(sp.VN_TIER_2)


# ---------------------------------------------------------------------------
# Snapshot test (exact counts) — Gate evidence
# ---------------------------------------------------------------------------


def test_resolved_tier_counts_per_market(capsys: pytest.CaptureFixture[str]) -> None:
    global_t1 = sp.GLOBAL_TIER_1
    global_t2 = sp.GLOBAL_TIER_2
    fi_t1 = sp.GLOBAL_TIER_1 | sp.FI_EXTENSION_TIER_1
    fi_t2 = sp.GLOBAL_TIER_2 | sp.FI_EXTENSION_TIER_2
    vn_t1 = sp.VN_TIER_1
    vn_t2 = sp.VN_TIER_2

    print(f"GLOBAL TIER_1 ({len(global_t1)}): {sorted(global_t1)}")
    print(f"GLOBAL TIER_2 ({len(global_t2)}): {sorted(global_t2)}")
    print(f"VN TIER_1 ({len(vn_t1)}): {sorted(vn_t1)}")
    print(f"VN TIER_2 ({len(vn_t2)}): {sorted(vn_t2)}")
    print(f"FI TIER_1 ({len(fi_t1)}): {sorted(fi_t1)}")
    print(f"FI TIER_2 ({len(fi_t2)}): {sorted(fi_t2)}")

    assert len(global_t1) == 16
    assert len(global_t2) == 20
    assert len(vn_t1) == 10
    assert len(vn_t2) == 12
    assert len(sp.FI_EXTENSION_TIER_1) == 2
    assert len(sp.FI_EXTENSION_TIER_2) == 3
    assert len(fi_t1) == 16 + 2
    assert len(fi_t2) == 20 + 3


# ---------------------------------------------------------------------------
# Independence guard (no imports from services.market_recap)
# ---------------------------------------------------------------------------


def test_module_does_not_import_market_recap() -> None:
    module_path = Path(sp.__file__)
    tree = ast.parse(module_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("services.market_recap"), f"forbidden import: {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert not mod.startswith("services.market_recap"), f"forbidden from-import: {mod}"


# ---------------------------------------------------------------------------
# registrable_domain helper sanity (duplicated impl — VN multi-suffix aware)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["", "not-a-url", "://broken", "ftp://"])
def test_malformed_urls_are_safe(url: str) -> None:
    assert sp.is_discarded(url) is False
    assert sp.tier_for(url, "GLOBAL") is None
    assert sp.tier_for(url, "VN") is None
    assert sp.tier_for(url, "FI") is None
    assert sp.is_trusted(url, "GLOBAL") is False


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.reuters.com/x", "reuters.com"),
        ("https://markets.ft.com/x", "ft.com"),
        ("https://www.hsc.com.vn/x", "hsc.com.vn"),
        ("https://ssc.gov.vn/x", "ssc.gov.vn"),
        ("https://hsx.vn/x", "hsx.vn"),
        ("https://abc.org.vn/x", "abc.org.vn"),
        ("not-a-url", ""),
    ],
)
def test_registrable_domain(url: str, expected: str) -> None:
    assert sp.registrable_domain(url) == expected
