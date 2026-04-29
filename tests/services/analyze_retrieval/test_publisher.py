from services.analyze_retrieval.publisher import publisher_label_for


def test_publisher_label_for_reuters() -> None:
    assert publisher_label_for("https://www.reuters.com/article/x") == "Reuters"


def test_publisher_label_for_yahoo_finance_full_host_key() -> None:
    assert publisher_label_for("https://finance.yahoo.com/quote/AAPL") == "Yahoo Finance"


def test_publisher_label_for_vn_source() -> None:
    assert publisher_label_for("https://cafef.vn/foo") == "CafeF"


def test_publisher_label_for_unknown_domain_falls_back_to_title_case() -> None:
    assert publisher_label_for("https://example-site.com/") == "Example Site"


def test_publisher_label_for_invalid_url_returns_empty_string() -> None:
    assert publisher_label_for("not a url") == ""
