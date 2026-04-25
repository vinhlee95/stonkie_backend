from services.market_recap.source_policy import is_allowlisted, registrable_domain


def test_registrable_domain_extracts_base_domain():
    assert registrable_domain("https://markets.ft.com/data/equities") == "ft.com"
    assert registrable_domain("https://www.reuters.com/world/us/") == "reuters.com"


def test_is_allowlisted_matches_allowlist_domains():
    assert is_allowlisted("https://markets.ft.com/data/equities") is True
    assert is_allowlisted("https://www.reuters.com/world/us/") is True
    assert is_allowlisted("https://example.com/article") is False


def test_is_allowlisted_supports_vn_market_domains():
    assert is_allowlisted("https://cafef.vn/thi-truong-chung-khoan.chn", market="VN") is True
    assert is_allowlisted("https://vietstock.vn/chung-khoan.htm", market="VN") is True
    assert is_allowlisted("https://example.com/article", market="VN") is False
