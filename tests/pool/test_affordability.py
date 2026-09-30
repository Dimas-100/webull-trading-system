import pytest

from webull_api.pool import affordability as A


def test_affordable_known_prices_whole_shares():
    prices = {"AAPL": 234.07, "XLU": 42.62}
    got = A.affordable(prices, 1_600.0, 6)         # slot 266.666...
    assert got == {"AAPL": 1, "XLU": 6}             # floor(266.67/234.07)=1, floor(266.67/42.62)=6
    got4 = A.affordable(prices, 1_600.0, 4)         # slot 400.0
    assert got4 == {"AAPL": 1, "XLU": 9}


def test_affordable_excludes_zero_share_names():
    prices = {"AFFORD": 100.0, "TOO_PRICEY": 10_000.0}
    got = A.affordable(prices, 1_600.0, 6)          # slot 266.67
    assert "TOO_PRICEY" not in got
    assert got == {"AFFORD": 2}


def test_affordable_excludes_unpriced_names():
    prices = {"OK": 50.0, "MISSING": None, "ZERO": 0.0, "NEGATIVE": -5.0}
    got = A.affordable(prices, 1_600.0, 6)
    assert set(got) == {"OK"}


def test_affordable_epsilon_guard_on_exact_division():
    # slot = 124.2 / 6 = 20.7; 20.7 / 6.9 is mathematically 3 but 2.9999999999999996 in binary.
    got = A.affordable({"X": 6.9}, 124.2, 6)
    assert got == {"X": 3}


def test_affordable_rejects_nonpositive_divisor():
    with pytest.raises(ValueError):
        A.affordable({"X": 10.0}, 100.0, 0)


def test_table_shape_one_row_per_equity():
    prices = {"CHEAP": 50.0, "MID": 500.0, "PRICEY": 5_000.0}
    rows = A.table(prices, [1_600.0, 10_000.0], divisors=(6, 4))
    assert [r["equity"] for r in rows] == [1_600.0, 10_000.0]
    for row in rows:
        assert row["total"] == 3
        assert set(row["by_divisor"]) == {6, 4}
        for d in (6, 4):
            cell = row["by_divisor"][d]
            assert cell["slot"] == pytest.approx(row["equity"] / d)
            assert cell["affordable"] == len(cell["symbols"])
            assert cell["symbols"] == sorted(cell["symbols"])


def test_table_counts_grow_with_equity_and_total_excludes_unpriced():
    prices = {"CHEAP": 50.0, "MID": 500.0, "PRICEY": 5_000.0, "UNPRICED": None}
    rows = A.table(prices, [600.0, 6_000.0, 60_000.0], divisors=(6,))
    by_equity = {r["equity"]: r for r in rows}
    assert by_equity[600.0]["total"] == 3                        # UNPRICED never counted
    assert by_equity[600.0]["by_divisor"][6]["affordable"] == 1  # slot 100: only CHEAP
    assert by_equity[6_000.0]["by_divisor"][6]["affordable"] == 2  # slot 1,000: CHEAP + MID
    assert by_equity[60_000.0]["by_divisor"][6]["affordable"] == 3  # slot 10,000: all three
    counts = [by_equity[e]["by_divisor"][6]["affordable"] for e in (600.0, 6_000.0, 60_000.0)]
    assert counts == sorted(counts)                               # monotonic non-decreasing


def test_table_default_divisors_are_six_and_four():
    rows = A.table({"X": 100.0}, [600.0])
    assert set(rows[0]["by_divisor"]) == {6, 4}
