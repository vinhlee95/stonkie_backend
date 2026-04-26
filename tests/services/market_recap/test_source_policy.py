from services.market_recap.source_policy import is_allowlisted, registrable_domain


def test_registrable_domain_extracts_base_domain():
    assert registrable_domain("https://markets.ft.com/data/equities") == "ft.com"
    assert registrable_domain("https://www.reuters.com/world/us/") == "reuters.com"


def test_registrable_domain_handles_vn_multi_suffixes_and_regressions():
    assert registrable_domain("https://ssi.com.vn/x") == "ssi.com.vn"
    assert registrable_domain("https://www.hsc.com.vn/x") == "hsc.com.vn"
    assert registrable_domain("https://ssc.gov.vn/x") == "ssc.gov.vn"
    assert registrable_domain("https://www.vir.com.vn/x") == "vir.com.vn"
    assert registrable_domain("https://abc.org.vn/x") == "abc.org.vn"
    assert registrable_domain("https://abc.net.vn/x") == "abc.net.vn"
    assert registrable_domain("https://abc.edu.vn/x") == "abc.edu.vn"
    assert registrable_domain("https://hsx.vn/x") == "hsx.vn"
    assert registrable_domain("https://www.cafef.vn/x") == "cafef.vn"
    assert registrable_domain("https://www.reuters.com/x") == "reuters.com"


def test_is_allowlisted_matches_allowlist_domains():
    assert is_allowlisted("https://markets.ft.com/data/equities") is True
    assert is_allowlisted("https://www.reuters.com/world/us/") is True
    assert is_allowlisted("https://example.com/article") is False


def test_is_allowlisted_supports_vn_market_domains():
    assert is_allowlisted("https://cafef.vn/thi-truong-chung-khoan.chn", market="VN") is True
    assert is_allowlisted("https://vietstock.vn/chung-khoan.htm", market="VN") is True
    assert is_allowlisted("https://example.com/article", market="VN") is False


def test_is_allowlisted_supports_expanded_vn_allowlist():
    added_domains = [
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
    ]
    for domain in added_domains:
        assert is_allowlisted(f"https://{domain}/x", market="VN") is True
        assert is_allowlisted(f"https://www.{domain}/x", market="VN") is True


def test_is_allowlisted_rejects_random_vn_blog():
    assert is_allowlisted("https://random-blog.vn/x", market="VN") is False


def test_is_allowlisted_supports_fi_market_domains():
    assert is_allowlisted("https://www.nasdaqomxnordic.com/shares", market="FI") is True
    assert is_allowlisted("https://www.inderes.fi/articles/example", market="FI") is True
    assert is_allowlisted("https://www.bloomberg.com/europe", market="FI") is True
    assert is_allowlisted("https://global.morningstar.com/fi/news/example", market="FI") is True
    assert is_allowlisted("https://www.tradingeconomics.com/finland/stock-market", market="FI") is True
    assert is_allowlisted("https://www.investing.com/equities/finland", market="FI") is True
    assert is_allowlisted("https://example.com/article", market="FI") is False
