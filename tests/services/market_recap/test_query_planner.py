from datetime import date

from services.market_recap.query_planner import HIGH_SIGNAL_SITES, plan_queries


def test_plan_queries_emits_open_and_site_scoped_queries():
    queries = plan_queries(date(2026, 4, 20), date(2026, 4, 24))

    assert len(queries) == 7
    assert queries[0].query == "US stock market recap week of Apr 20-24, 2026"
    assert queries[0].include_domains == []

    scoped_queries = queries[1:]
    assert len(scoped_queries) == len(HIGH_SIGNAL_SITES)
    assert [query.include_domains for query in scoped_queries] == [[site] for site in HIGH_SIGNAL_SITES]
    assert all(query.query == "US market week recap Apr 20-24 2026" for query in scoped_queries)


def test_plan_queries_supports_vn_market():
    queries = plan_queries(date(2026, 4, 20), date(2026, 4, 24), market="VN")

    assert len(queries) == 7
    assert queries[0].query == "Vietnam stock market recap week of Apr 20-24, 2026"
    assert queries[0].include_domains == []
    scoped_domains = [query.include_domains[0] for query in queries[1:]]
    assert scoped_domains[:3] == ["fireant.vn", "vietstock.vn", "vneconomy.vn"]
