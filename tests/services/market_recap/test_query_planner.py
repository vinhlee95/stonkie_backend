from datetime import date

from services.market_recap.query_planner import (
    FI_HIGH_SIGNAL_SITES,
    HIGH_SIGNAL_SITES,
    VN_HIGH_SIGNAL_SITES,
    plan_queries,
)


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

    assert len(queries) == 1 + len(VN_HIGH_SIGNAL_SITES)
    assert queries[0].query == "thị trường chứng khoán Việt Nam tuần qua"
    assert queries[0].include_domains == []
    scoped_queries = queries[1:]
    assert [query.include_domains for query in scoped_queries] == [[site] for site in VN_HIGH_SIGNAL_SITES]
    assert all(query.query == "thị trường chứng khoán Việt Nam tuần qua" for query in scoped_queries)


def test_plan_queries_supports_vn_daily_template():
    queries = plan_queries(date(2026, 4, 24), date(2026, 4, 24), market="VN", cadence="daily")
    assert len(queries) == 1 + len(VN_HIGH_SIGNAL_SITES)
    assert queries[0].query == "thị trường chứng khoán Việt Nam phiên hôm nay"
    assert queries[0].include_domains == []
    scoped_queries = queries[1:]
    assert [query.include_domains for query in scoped_queries] == [[site] for site in VN_HIGH_SIGNAL_SITES]
    assert all(query.query == "thị trường chứng khoán Việt Nam phiên hôm nay" for query in scoped_queries)


def test_plan_queries_supports_fi_market():
    queries = plan_queries(date(2026, 4, 20), date(2026, 4, 24), market="FI")

    assert len(queries) == 1 + len(FI_HIGH_SIGNAL_SITES)
    assert queries[0].query == "Helsinki stock exchange Finnish market recap week of Apr 20-24, 2026"
    assert queries[0].include_domains == []
    scoped_queries = queries[1:]
    assert [query.include_domains for query in scoped_queries] == [[site] for site in FI_HIGH_SIGNAL_SITES]
    assert all(
        query.query == "Finland stock market week recap Helsinki exchange OMX Helsinki Apr 20-24 2026"
        for query in scoped_queries
    )


def test_plan_queries_supports_us_daily_template():
    queries = plan_queries(date(2026, 4, 23), date(2026, 4, 23), market="US", cadence="daily")

    assert len(queries) == 1 + len(HIGH_SIGNAL_SITES)
    assert queries[0].query == "US stock market recap Apr 23, 2026"
    assert queries[0].include_domains == []
    scoped_queries = queries[1:]
    assert [query.include_domains for query in scoped_queries] == [[site] for site in HIGH_SIGNAL_SITES]
    assert all(query.query == "US market session recap Apr 23 2026" for query in scoped_queries)
    for query in queries:
        assert "week" not in query.query.lower()


def test_plan_queries_supports_fi_daily_template():
    queries = plan_queries(date(2026, 4, 23), date(2026, 4, 23), market="FI", cadence="daily")

    assert len(queries) == 1 + len(FI_HIGH_SIGNAL_SITES)
    assert queries[0].query == "Helsinki stock exchange Finnish market recap Apr 23, 2026"
    assert queries[0].include_domains == []
    scoped_queries = queries[1:]
    assert [query.include_domains for query in scoped_queries] == [[site] for site in FI_HIGH_SIGNAL_SITES]
    assert all(
        query.query == "Finland stock market session recap Helsinki exchange OMX Helsinki Apr 23 2026"
        for query in scoped_queries
    )
    for query in queries:
        assert "week" not in query.query.lower()


def test_plan_queries_fi_includes_requested_high_signal_sources():
    queries = plan_queries(date(2026, 4, 20), date(2026, 4, 24), market="FI")
    scoped_domains = {query.include_domains[0] for query in queries[1:] if query.include_domains}
    assert "global.morningstar.com" in scoped_domains
    assert "investing.com" in scoped_domains
    assert "tradingeconomics.com" in scoped_domains
