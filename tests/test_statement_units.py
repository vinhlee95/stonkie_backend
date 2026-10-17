from core.statement_units import normalize_periods, rescale, scale_ratio, unit_multiplier

# MSFT, values in thousands (real rows).
Q1 = {"Total Revenue": 82886000, "Net Income Common Stockholders": 31778000, "Basic EPS": 4.28, "Tax Rate for Calcs": 0}
Q4 = {"Total Revenue": 81273000, "Net Income Common Stockholders": 30881000, "Basic EPS": 4.14}
ANNUAL = {"Total Revenue": 281724000, "Net Income Common Stockholders": 101832000, "Basic EPS": 13.7}
# Scraped in full units: scaled values x1000, EPS / rate untouched.
FULL = {
    "Total Revenue": 90010000000,
    "Net Income Common Stockholders": "35,770,000,000",
    "Basic EPS": 4.82,
    "Tax Rate for Calcs": 0.19,
    "Note": None,
}
# Scraped in billions ("449.96B" -> 449.96).
BILLIONS = {"Total Revenue": 90.01, "Net Income Common Stockholders": 35.77, "Basic EPS": 4.82}


def test_mis_scaled_periods_are_detected():
    assert unit_multiplier(FULL, [Q1, Q4, ANNUAL]) == 0.001
    assert unit_multiplier(BILLIONS, [Q1, Q4, ANNUAL]) == 1_000_000


def test_thousands_period_is_consistent_against_quarters_or_annual():
    assert unit_multiplier(Q1, [Q4]) == 1
    assert unit_multiplier(Q1, [ANNUAL]) == 1
    assert unit_multiplier(Q1, [Q4, ANNUAL, FULL]) == 1


def test_ratio_far_from_a_power_of_1000_is_left_alone():
    assert unit_multiplier({"Total Revenue": 30}, [{"Total Revenue": 1}]) == 1


def test_no_shared_keys_means_unknown():
    assert scale_ratio({"Total Revenue": 1}, [{"Other": 2}]) is None
    assert unit_multiplier({"Total Revenue": 1}, []) == 1


def test_rescale_skips_eps_rates_and_non_numbers():
    assert rescale(BILLIONS, 1_000_000) == {
        "Total Revenue": 90010000,
        "Net Income Common Stockholders": 35770000,
        "Basic EPS": 4.82,
    }
    assert rescale(FULL, 0.001) == {
        "Total Revenue": 90010000,
        "Net Income Common Stockholders": 35770000,
        "Basic EPS": 4.82,
        "Tax Rate for Calcs": 0.19,
        "Note": None,
    }


def test_normalize_periods_rescales_only_the_outlier_in_batch():
    items = [
        {"period_end_quarter": "6/30/2026", "metrics": FULL},
        {"period_end_quarter": "3/31/2026", "metrics": Q1},
    ]
    result = normalize_periods(items, [Q4, ANNUAL])
    assert result[0]["metrics"]["Total Revenue"] == 90010000
    assert result[0]["period_end_quarter"] == "6/30/2026"
    assert result[1] == items[1]


def test_normalize_periods_uses_annual_reference_for_fresh_ticker():
    items = [{"period_end_quarter": "6/30/2026", "metrics": FULL}]
    assert normalize_periods(items, [ANNUAL])[0]["metrics"]["Total Revenue"] == 90010000
    assert normalize_periods(items, []) == items
