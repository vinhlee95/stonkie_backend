from services.analyze_retrieval.market import resolve_market


def test_resolve_market_maps_usa_to_global() -> None:
    assert resolve_market("USA", "") == "GLOBAL"


def test_resolve_market_maps_vietnam_inputs_to_vn() -> None:
    assert resolve_market("Vietnam", "") == "VN"
    assert resolve_market(" vietnam ", "") == "VN"


def test_resolve_market_maps_finland_to_fi() -> None:
    assert resolve_market("FI", "") == "FI"


def test_resolve_market_defaults_to_global_for_unknown_country() -> None:
    assert resolve_market("Atlantis", "") == "GLOBAL"


def test_resolve_market_defaults_to_global_when_country_missing() -> None:
    assert resolve_market(None, "Cổ phiếu Vinamilk tăng mạnh") == "GLOBAL"
    assert resolve_market("", "Tình hình thị trường") == "GLOBAL"


def test_resolve_market_uses_country_even_when_question_has_vietnamese_text() -> None:
    assert resolve_market("USA", "Cổ phiếu Vinamilk tăng mạnh") == "GLOBAL"
