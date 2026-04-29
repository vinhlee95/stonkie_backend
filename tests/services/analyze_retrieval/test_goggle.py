from services.analyze_retrieval.goggle import build_chat_goggle


def test_build_chat_goggle_global_contains_reuters_boost_line() -> None:
    output = build_chat_goggle("GLOBAL")
    assert "$boost=4,site=reuters.com" in output


def test_build_chat_goggle_skips_wildcard_gov() -> None:
    assert "*.gov" not in build_chat_goggle("GLOBAL")
    assert "*.gov" not in build_chat_goggle("VN")
    assert "*.gov" not in build_chat_goggle("FI")


def test_build_chat_goggle_vn_does_not_inherit_global_sources() -> None:
    output = build_chat_goggle("VN")
    assert "$boost=4,site=reuters.com" not in output
    assert "$boost=4,site=cafef.vn" in output


def test_build_chat_goggle_fi_stacks_global_and_fi_extension() -> None:
    output = build_chat_goggle("FI")
    assert "$boost=4,site=reuters.com" in output
    assert "$boost=4,site=inderes.fi" in output
    assert "$boost=2,site=hs.fi" in output


def test_build_chat_goggle_deterministic_output() -> None:
    assert build_chat_goggle("GLOBAL") == build_chat_goggle("GLOBAL")


def test_build_chat_goggle_omits_path_prefix_discards() -> None:
    output = build_chat_goggle("GLOBAL")
    assert "$discard=reddit.com" in output
    assert "tradingview.com/ideas" not in output
